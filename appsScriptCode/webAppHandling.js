function doGet(e) {
  var params = JSON.stringify(e);
  Logger.log(params)
  var page = (e && e.parameter && e.parameter.page) || "home"
  var file = (page === "rules" || page === "ruleBuilder") ? "ruleBuilder.html" : "index.html"
  var title = file === "ruleBuilder.html" ? "Rule Combination Builder" : "Stock Automation"
  return HtmlService.createHtmlOutputFromFile(file)
    .setTitle(title)
    .addMetaTag("viewport", "width=device-width, initial-scale=1")
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL)
}

function doPost(e) {
  return
}