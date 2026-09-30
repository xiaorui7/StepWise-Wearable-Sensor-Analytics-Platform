App({
  onLaunch() {
    wx.cloud.init({
      env: 'YOUR_WECHAT_CLOUD_ENV',
      traceUser: true
    })
  },
  globalData: {
    apiBase: 'https://YOUR_CLOUD_HOSTING_DOMAIN',
    cloudEnv: 'YOUR_WECHAT_CLOUD_ENV',
    cloudService: 'YOUR_CLOUD_SERVICE_NAME',
    lastResult: null
  }
})
