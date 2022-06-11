var cache = CacheService.getScriptCache()

function myFunction() {
  var ss = SpreadsheetApp.create("testingFinal").insertSheet("temporary")
  var collectedValues = []
  var prevValues = cache.get("prevValues")
  var currentValues = ss.getRange(1,1,1,3).setValues([
      [10,
      11,
      12
      ]
  ]).getValues()
  Logger.log("currentValues : " + currentValues)
  collectedValues.push(...currentValues[0])
  if (prevValues) {
    prevValues = JSON.parse(prevValues)
    Logger.log("prevValues : " + prevValues)
    collectedValues.push(...prevValues)
  }
  Logger.log("collectedValues : " + collectedValues)
  cache.put("prevValues", JSON.stringify(collectedValues))
}

function remove() {
  cache.remove("prevValues")
}

function parseinteger() {
  Logger.log(parseInt("1"))
}

function cacheObject() {
  return CacheService.getScriptCache()
}

function a() {
  var cacheobj = cacheObject()
  
}