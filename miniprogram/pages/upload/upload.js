const app = getApp()
const { saveHistory } = require('../../utils/history')

function chooseTxtFile(callback) {
  wx.chooseMessageFile({
    count: 1,
    type: 'file',
    extension: ['txt'],
    success(res) {
      const file = res.tempFiles[0]
      callback(file)
    },
    fail() {
      wx.showToast({ title: 'No file selected', icon: 'none' })
    }
  })
}

function readTextFile(path) {
  return wx.getFileSystemManager().readFileSync(path, 'utf8')
}

function shortError(err) {
  if (!err) return 'Unknown error'
  if (typeof err === 'string') return err
  return err.errMsg || err.message || JSON.stringify(err)
}

Page({
  data: {
    walkingFile: null,
    standingFile: null,
    walkingName: '',
    standingName: '',
    channels: ['P1', 'P2', 'P3', 'P4'],
    signs: ['positive', 'negative'],
    mapping: {
      heel: 'P2',
      arch: 'P3',
      medialForefoot: 'P4',
      lateralForefoot: 'P1'
    },
    heelIndex: 1,
    archIndex: 2,
    medialForefootIndex: 3,
    lateralForefootIndex: 0,
    pitchEversionSign: 'positive',
    pitchSignIndex: 0,
    loading: false
  },

  chooseWalking() {
    chooseTxtFile((file) => {
      this.setData({ walkingFile: file, walkingName: file.name })
    })
  },

  chooseStanding() {
    chooseTxtFile((file) => {
      this.setData({ standingFile: file, standingName: file.name })
    })
  },

  changeMapping(event) {
    const key = event.currentTarget.dataset.key
    const index = Number(event.detail.value)
    const value = this.data.channels[index]
    const updates = {}
    updates[`mapping.${key}`] = value
    updates[`${key}Index`] = index
    this.setData(updates)
  },

  changePitchSign(event) {
    const index = Number(event.detail.value)
    this.setData({
      pitchSignIndex: index,
      pitchEversionSign: this.data.signs[index]
    })
  },

  handleAnalysisResult(data) {
    this.setData({ loading: false })
    if (!data || !data.ok) {
      wx.showModal({
        title: 'Analysis failed',
        content: data && data.error ? data.error.slice(0, 900) : 'No valid response from backend.',
        showCancel: false
      })
      return
    }
    const historyItem = saveHistory(data)
    data.generated_at = historyItem.createdAt
    app.globalData.lastResult = data
    wx.navigateTo({ url: '/pages/result/result' })
  },

  handleV1Result(data) {
    const cards = (data.result && data.result.screening_cards) || []
    const metrics = (data.result && data.result.metrics) || {}
    const summary = (data.result && data.result.summary) || {}
    const urls = data.artifact_urls || {}
    const top = cards[0] || { title: 'No clear StepWise-supported tendency', level: 'Low' }
    this.handleAnalysisResult({
      ok: true,
      run_id: data.analysis_id,
      top_result: top.title,
      top_level: top.level,
      summary,
      cards,
      metrics: {
        PitchDelta: metrics.PitchDelta_stance_from_standing,
        LandingAngle: metrics.LandingSoleGroundAngle_deg,
        ArchRatio: metrics.ArchRatio_mean
      },
      urls: {
        user_report: urls['report.html'] ? `${app.globalData.apiBase}${urls['report.html']}` : ''
      }
    })
  },

  pollAnalysis(analysisId, attempt = 0) {
    if (attempt > 150) {
      this.setData({ loading: false })
      wx.showModal({ title: 'Analysis timeout', content: 'Please check History later.', showCancel: false })
      return
    }
    wx.cloud.callContainer({
      config: { env: app.globalData.cloudEnv },
      path: `/api/v1/analyses/${analysisId}`,
      method: 'GET',
      header: { 'X-WX-SERVICE': app.globalData.cloudService },
      success: (statusResponse) => {
        if (statusResponse.data.status === 'FAILED') {
          this.setData({ loading: false })
          wx.showModal({ title: 'Analysis failed', content: statusResponse.data.error_message || 'The worker could not finish this analysis.', showCancel: false })
        } else if (statusResponse.data.status === 'SUCCEEDED') {
          wx.cloud.callContainer({
            config: { env: app.globalData.cloudEnv },
            path: `/api/v1/analyses/${analysisId}/result`,
            method: 'GET',
            header: { 'X-WX-SERVICE': app.globalData.cloudService },
            success: (resultResponse) => this.handleV1Result(resultResponse.data),
            fail: (err) => this.requestResultByHttps(analysisId, err)
          })
        } else {
          setTimeout(() => this.pollAnalysis(analysisId, attempt + 1), 800)
        }
      },
      fail: () => setTimeout(() => this.pollAnalysisByHttps(analysisId, attempt), 800)
    })
  },

  pollAnalysisByHttps(analysisId, attempt = 0) {
    wx.request({
      url: `${app.globalData.apiBase}/api/v1/analyses/${analysisId}`,
      success: (res) => {
        if (res.data.status === 'SUCCEEDED') this.requestResultByHttps(analysisId)
        else if (res.data.status === 'FAILED') this.handleAnalysisResult({ ok: false, error: res.data.error_message })
        else setTimeout(() => this.pollAnalysisByHttps(analysisId, attempt + 1), 800)
      },
      fail: (err) => this.handleAnalysisResult({ ok: false, error: shortError(err) })
    })
  },

  requestResultByHttps(analysisId, cloudError) {
    wx.request({
      url: `${app.globalData.apiBase}/api/v1/analyses/${analysisId}/result`,
      success: (res) => this.handleV1Result(res.data),
      fail: (err) => this.handleAnalysisResult({ ok: false, error: `${shortError(cloudError)} ${shortError(err)}` })
    })
  },

  requestByHttps(payload, cloudError) {
    wx.request({
      url: `${app.globalData.apiBase}/api/v1/analyses/text`,
      method: 'POST',
      header: {
        'content-type': 'application/json'
      },
      data: payload,
      success: (res) => {
        this.pollAnalysisByHttps(res.data.analysis_id)
      },
      fail: (err) => {
        wx.showModal({
          title: 'Request failed',
          content: `Cloud call failed: ${shortError(cloudError)}\n\nHTTPS fallback failed: ${shortError(err)}`,
          showCancel: false
        })
        console.error('callContainer failed:', cloudError)
        console.error('HTTPS fallback failed:', err)
      },
      complete: () => {
        this.setData({ loading: false })
      }
    })
  },

  analyze() {
    if (!this.data.walkingFile) {
      wx.showToast({ title: 'Choose walking TXT first', icon: 'none' })
      return
    }

    let walkingText = ''
    let standingText = ''
    try {
      walkingText = readTextFile(this.data.walkingFile.path)
      if (this.data.standingFile) {
        standingText = readTextFile(this.data.standingFile.path)
      }
    } catch (err) {
      wx.showModal({ title: 'Read file failed', content: String(err), showCancel: false })
      return
    }

    const selectedChannels = [
      this.data.mapping.heel,
      this.data.mapping.arch,
      this.data.mapping.medialForefoot,
      this.data.mapping.lateralForefoot
    ]
    if (new Set(selectedChannels).size < selectedChannels.length) {
      wx.showModal({
        title: 'Check sensor mapping',
        content: 'One pressure channel is assigned to more than one foot region. Please confirm P1-P4 mapping before analysis.',
        showCancel: false
      })
      return
    }

    const payload = {
      walkingText,
      standingText,
      heel: this.data.mapping.heel,
      arch: this.data.mapping.arch,
      medialForefoot: this.data.mapping.medialForefoot,
      lateralForefoot: this.data.mapping.lateralForefoot,
      pitchEversionSign: this.data.pitchEversionSign
    }

    this.setData({ loading: true })
    wx.cloud.callContainer({
      config: {
        env: app.globalData.cloudEnv
      },
      path: '/api/v1/analyses/text',
      method: 'POST',
      header: {
        'X-WX-SERVICE': app.globalData.cloudService,
        'content-type': 'application/json'
      },
      data: payload,
      success: (res) => {
        this.pollAnalysis(res.data.analysis_id)
      },
      fail: (err) => {
        this.requestByHttps(payload, err)
      }
    })
  }
})
