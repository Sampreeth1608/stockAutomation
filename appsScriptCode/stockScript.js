class stockAutomation {
  
  constructor (sheet=null) {
    this.spreadsheet = sheet ? sheet : SpreadsheetApp.openById("")
    this.stocks = []
    this.sheet = this.spreadsheet.getSheetByName("temporary")
    if (!this.sheet) {
      sheet = this.spreadsheet.insertSheet("temporary")
    }
    var range = sheet.getRange(1,1,1,6)
    if (range.getValue() == '') {
      range.setValues([["Date", "Price", "Volume", "Quote Structure", "Volume Structure", "Refined Structure"]])
    }
  }

  fetchAndSetValues () {
    var write_row = this.sheet.getLastRow()+1
    this.sheet.getRange(write_row, 1, 1, 3).setValues(
      [
        [Date(), 
        '=GOOGLEFINANCE("NASDAQ:AMZN", "price")',
        '=GOOGLEFINANCE("NASDAQ:AMZN", "volume")'
        ]
      ]
    )
    this.Calculations(write_row)
  }

  calculations(write_row) {
  // to stop the auto-updations of cell
    var values = this.sheet.getRange(write_row, 1, 1, 3).getValues()
    if (write_row >= 3) {
    var prevValues = this.sheet.getRange(write_row-1, 2,1,2).getValues()[0] // return 2 elements in an array
    Logger.log(prevValues)
    Logger.log("values" + values.toString())
    var quoteStructure = values[0][1]-prevValues[0]
    var volumeStructure = values[0][2]-prevValues[1]
    var refinedStructure = quoteStructure
    if (quoteStructure >= 0) {
      refinedStructure = volumeStructure * -1
    }
    this.sheet.getRange(write_row, 4, 1, 3).setValues([[quoteStructure, volumeStructure, refinedStructure]])
  }
  this.sheet.getRange(write_row, 1, 1, 3).setValues(values)
  }

}