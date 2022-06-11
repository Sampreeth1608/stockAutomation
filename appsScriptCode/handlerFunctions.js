const sendErrorToMail= (errorMsg) => {
  Logger.log(errorMsg)
  return errorMsg
}

const createSpreadsheet= () => {
  const spreadsheet = SpreadsheetApp.create("stockAutomationTesting")
  return spreadsheet
}