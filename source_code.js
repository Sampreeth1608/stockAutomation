function testing1() {
  Logger.log(Date())
}

function onSheet1() {
  // create sheet if doesn't exist
  var spreadsheet = SpreadsheetApp.openById("1qutpCUrPPgjZY9ngcuFqp_Ryjx39BO0o20qQdc_Il6E")
  var tempSheet = spreadsheet.getSheetByName("temporary")
  if (!tempSheet) {
    var tempSheet = spreadsheet.insertSheet("temporary")
  }
  var range = tempSheet.getRange(1,1,1,1)
  if (range.getValue() == '') {
    range.setValue("Date")
    tempSheet.getRange(1,2,1,1).setValue("Price")
    tempSheet.getRange(1,3,1,1).setValue("Volume")
  }

  // call googlefinance function to retrive data
  // Get/Set current date&time -> r2c1
  write_row = tempSheet.getLastRow()+1
  tempSheet.getRange(write_row, 1, 1, 1).setValue(Date())
  
  tempSheet.getRange(write_row, 2, 1, 1).setValue('=GOOGLEFINANCE("NYSE:RBLX", "price")')
  // to remove the auto-updation of cell
  var values = tempSheet.getRange(write_row, 2, 1, 1).getValues()
  tempSheet.getRange(write_row, 2, 1, 1).setValues(values)

  tempSheet.getRange(write_row, 3, 1, 1).setValue('=GOOGLEFINANCE("NYSE:RBLX", "volume")')
  // to remove the auto-updation of cell
  values = tempSheet.getRange(write_row, 3, 1, 1).getValues()
  tempSheet.getRange(write_row, 3, 1, 1).setValues(values)
}

function omnSheet1() {
  var spreadSheet = SpreadsheetApp.openById("1qutpCUrPPgjZY9ngcuFqp_Ryjx39BO0o20qQdc_Il6E")
  var sheet = spreadSheet.getSheetByName("googleFinance")
  var range = sheet.getRange(1, 1, 1, 1)
  if (range.getValue() == '') {
    range.setValue("Date")
  }
  //range.setValue('=GOOGLEFINANCE("NSE:IRCTC", "all", DATE(2021, 11, 22), 1, 1)')
  //return spreadSheet, sheet
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
  if (sheet1.getLastRow() == sheet2.getLastRow()) {
    Logger.log("Goodbye")
    deleteTriggers()
  }
}

function trigger(e) {
  //onSheet1()
  var trigger = ScriptApp.newTrigger("onSheet1")
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
