function doGet(e) {
  var params = JSON.stringify(e);
  Logger.log(params)
  return HtmlService.createHtmlOutputFromFile("index.html")
}

function doPost(e) {
  return
}