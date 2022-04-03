const connectToDb= (stockname, spreadsheetId) => {
  var params = {
    "method" : "post",
    "payload" : {
      "name" : stockname,
      "source" : spreadsheetId
    }
  }

  const id = UrlFetchApp.fetch("https://us-east-1.aws.webhooks.mongodb-realm.com/api/client/v2.0/app/stockautomationapplication-wibil/service/appsScript-connect/incoming_webhook/createDatabase", params)
  Logger.log(id)
}

const insertToCollection= (data) => {
  // data should be in the form of {"stockName" : "", "values" : [1,2,3]}
  var params = {
    "method" : "post",
    "payload" : JSON.stringify(data)
  }

  var result = UrlFetchApp.fetch("https://us-east-1.aws.webhooks.mongodb-realm.com/api/client/v2.0/app/stockautomationapplication-wibil/service/appsScript-connect/incoming_webhook/addDataToDocuments", params)
  result = JSON.parse(result)
  Logger.log(result)
  if (result.matchedCount.$numberInt > 0) {
    if (result.modifiedCount.$numberInt > 1) {
      throw Error("no documents modified") // replace this with sendErrorToMail()
    }
  } else {
    throw Error("no document matched") // replace this with sendErrorToMail()
  }
}

const getStockData = (stockname) => {
  // sanitize the stockname.
  const handlerFunction= (key, value) => {
    // format of value [{"$numberDouble":"436.2"}]
    if (testKeyNames.has(key)) {
      testKeyNames.set(key, testKeyNames.get(key)+1)
      var numbers = []
      for (var i=0; i<value.length; i++) {
        const eachValue = value[i]
        if (eachValue["$numberDouble"]) {
          numbers.push(eachValue["$numberDouble"])
        } else if (eachValue["$undefined"]) {
          numbers.push(eachValue["$undefined"])
        }
      }
    }
    if (testKeyNames.get(key) != 1) {
      throw Error("Something went wrong")
    }
    return numbers
  }

  const handlerFunctionTemporary = (data) => {
    var number = []
    for (var i=0; i<data[0].length; i++) {
      number.push([data[0][i], data[1][i], data[2][i], data[3][i], data[4][i]])
    }
    return number
  }

  var testKeyNames = new Map()
  testKeyNames.set("price", 0)
  testKeyNames.set("volume", 0)
  testKeyNames.set("quoteStructure", 0)
  testKeyNames.set("volumeStructure", 0)
  testKeyNames.set("refinedStructure", 0)

  var params = {
    "method" : "post",
    "payload": {
      "name" : stockname
    }
  }

  var result = UrlFetchApp.fetch("https://us-east-1.aws.webhooks.mongodb-realm.com/api/client/v2.0/app/stockautomationapplication-wibil/service/appsScript-connect/incoming_webhook/getDataFromDocuments", params)
  if (result) {
    var data = [] // this will going to have sub-lists as an element
    result = JSON.parse(result)
    testKeyNames.forEach((value, key) => data.push(handlerFunction(key, result[key])))
    data = handlerFunctionTemporary(data)
    return data
  } else {
    throw Error("no documents found")
  }
}

const TestingInsertDbWebhook1 = () => {
  //connectToDb("LIC", "qwerty")
  //insertToCollection({"stockname" : "LIC", "values" : [11,22,33,44,55]})
  getStockData("NSE:TATAMOTORS")
}
