exports = async function createDb(payload) {
  const mongodb = context.services.get("mongodb-atlas");
  const database = mongodb.db("stockDatabase");
  const collection = database.collection("stockCollections");
  const name = payload.query.name
  const sheetLink = payload.query.source
  const documentStructure = {
    "name" : name,
    "source" : sheetLink,
    "date" : new Date(),
    "price" : [],
    "volume" : [],
    "quoteStructure" : [],
    "volumeStructure" : [],
    "refinedStructure" : []
  }
  
  const result = collection.insertOne(documentStructure);
  var id = result.insertedId.toString();
  if (result && id) {
    return JSON.stringify(id);
  }
  return "Error in operation";
}
