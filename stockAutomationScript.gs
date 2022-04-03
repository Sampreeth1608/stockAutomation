var cache = CacheService.getScriptCache()

function creations() {
  //TODO: sanitize the username
  const sheetObj = createSpreadsheet()
  connectToDb("NSE:TATAMOTORS", sheetObj.getId()) // creates a default document inside the collection.
  const spreadsheet = SpreadsheetApp.openById(sheetObj.getId())
  var sheet = spreadsheet.getSheetByName("temporary")
  if (!sheet) {
    sheet = spreadsheet.insertSheet("temporary")
  }
  cache.putAll({
    "sheetObj": spreadsheet.getId(),
    "stockname": "NSE:TATAMOTORS",
    "stocknumber" : "1",
    "prevValues" : ""
  }, 21600)
}

function fetchAndSetValues() {
  Logger.log(cache.getAll(["sheetObj", "stockname", "stocknumber", "prevValues"]))
  const sheetId = cache.get("sheetObj") // get the sheet instance from cache & assign it to the sheet
  const stockname = cache.get("stockname")
  const stocknumber = cache.get("stocknumber")
  const sheet = SpreadsheetApp.openById(sheetId).getSheetByName("temporary")
  var collectedValues = [] // maintained this to collect and send the list of values to the document in mongodb
  // var write_row = sheet.getLastRow()+1
  var prevValues = cache.get("prevValues")
  var values = sheet.getRange(parseInt(stocknumber), 1, 1, 3).setValues(
    [
      [Date(), 
      '=GOOGLEFINANCE("NSE:TATAMOTORS", "price")',
      '=GOOGLEFINANCE("NSE:TATAMOTORS", "volume")'
      ]
    ]
  ).getValues() // returns a compound list.

  collectedValues.push(...values[0].slice(1,)) // pushes the current value
  if (prevValues) {
    prevValues = JSON.parse(prevValues)
    var quoteStructure = values[0][1]-prevValues[0]
    var volumeStructure = values[0][2]-prevValues[1]
    var refinedStructure = quoteStructure
    if (quoteStructure >= 0) {
      refinedStructure = volumeStructure * -1
    }
    collectedValues.push(...[quoteStructure, volumeStructure, refinedStructure])
  }
  Logger.log("values : " + values)
  Logger.log("prevValues : " + prevValues)
  cache.put("prevValues", JSON.stringify([values[0][1], values[0][2]]), 21600)
  insertToCollection({"stockname" : stockname, "values" : collectedValues})
}

function getValuesOnSpreadsheet() {
  const stockname = "NSE:TATAMOTORS" //cache.get("stockname")
  const sheetId = "16qS9OF_K2AqV089FXk43Bk-vpzVfbD2F3OGauhJl5AA" // cache.get("stockObj")
  const data = getStockData(stockname)
  const sheet = SpreadsheetApp.openById(sheetId).getSheetByName("Sheet1")
  sheet.getRange(1, 1, data.length, data[0].length).setValues(data).setValues(data)
}
