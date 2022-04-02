exports = async function addData(payload) {
  const mongodb = context.services.get("mongodb-atlas")
  const stockdb = mongodb.db("stockDatabase")
  const stockCollection = stockdb.collection("stockCollections")
  const data =  JSON.parse(Object.keys(payload.query)[0])
  const values = data.values
  const name = data.stockname
  var result = stockCollection.updateOne(
    {"name" : name},
    {
      "$push" : {
        "price" : values[0],
        "volume" : values[1],
        "quoteStructure" : values[2],
        "volumeStructure" : values[3],
        "refinedStructure" : values[4]
      }
    }
    )
  return result
}
