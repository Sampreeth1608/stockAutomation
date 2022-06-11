const getBSEStockInfo = (stockname) => {
  const spreadsheet = SpreadsheetApp.openById("1gQTeQ-Zq-Mjvgffp0m7j2PwC0DmeGDKaXBxVnkuAJkw")
  const sheet = spreadsheet.getSheetByName("BSEEquity")
  const columns = sheet.getLastColumn()
  const rows = sheet.getLastRow()
  Logger.log("columns : " + columns.toString() + ", rows : " + rows.toString())
  const listedStockNames = sheet.getRange(2, 2, rows, 1).getValues()
  var match = false
  for (var i=0; i<listedStockNames.length; i++) {
    if (listedStockNames[i][0] == stockname) {
      i += 2
      match = true
      break
    }
  }
  if (!match) {
    return false
  }
  Logger.log("index : " + i)
  var stockInfo = sheet.getRange(i, 1, 1, columns).getValues()[0] // same description than above. 
  return stockInfo
}

function getStockInfo() {
  const output = getBSEStockInfo("")
  Logger.log(output)
}