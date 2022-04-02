exports = async function getData(payload) {
  const mongodb = context.services.get("mongodb-atlas")
  const stockdb = mongodb.db("stockDatabase")
  const stockCollection = stockdb.collection("stockCollections")
  const query = payload.query
  //const projection = {"price" : 1, "volume" : 1}
  const result = stockCollection.findOne(query)
  return result
}
