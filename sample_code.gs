function onSheet1() {
  var spreadSheet = SpreadsheetApp.openById("1qutpCUrPPgjZY9ngcuFqp_Ryjx39BO0o20qQdc_Il6E")
  var sheet = spreadSheet.getSheetByName("googleFinance")
  var range = sheet.getRange(1, 1, 1, 1)
  range.setValue('=GOOGLEFINANCE("NASDAQ:FB", "all", DATE(2021, 10, 1), 20, "DAILY")')
  return spreadSheet, sheet
}

function onSheet2() {
  // write the data on temp. sheet on each minute
  var ss = SpreadsheetApp.openById("1qutpCUrPPgjZY9ngcuFqp_Ryjx39BO0o20qQdc_Il6E")
  var sheet1 = ss.getSheetByName("googleFinance")
  var sheet2 = ss.getSheetByName("temp")
  var lastrow = sheet2.getLastRow()
  Logger.log(lastrow)
  var startrow = lastrow+1
  if (lastrow == 0) {
    startrow = 1
  }
  //lastrow += 4
  var s1range = sheet1.getRange(startrow, 1, 4, 6)
  var s1values = s1range.getValues()
  Logger.log(s1values)
  var data = []
  for (var i=0; i<s1values.length; i++) {
    data.push([s1values[i][0].toString(), s1values[i][1].toString(), s1values[i][5].toString()])
  }
  var s2range = sheet2.getRange(startrow, 1, data.length, data[0].length)
  Logger.log(data)
  s2range.setValues(data)
}

function trigger(e) {
  onSheet1()
  var trigger = ScriptApp.newTrigger("onSheet2")
  .timeBased()
  .everyMinutes(1)
  .create()
}

function deleteTriggers() {
  var triggers = ScriptApp.getProjectTriggers()
  for (var i=0; i<triggers.length; i++) {
    Logger.log(triggers)
    ScriptApp.deleteTrigger(triggers[i])
  }
}
