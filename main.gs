// This script will be responsible to call functions from different scripts and make them connect with one another

const Trigger= () => {
  creations() // fucntion to create database+collection with a default document & also create a new spreadsheet
  var trigger = ScriptApp.newTrigger("fetchAndSetValues")
  .timeBased()
  .everyMinutes(5)
  .create()
}

const deleteTriggers= () => {
  var runningTriggers = ScriptApp.getScriptTriggers()
  for (var i=0; i<runningTriggers.length; i++) {
    Logger.log(runningTriggers[i])
    ScriptApp.deleteTrigger(runningTriggers[i])
  }
}

function bbb() {
  console.log("Hello")
}

function aaa() {
  ScriptApp.newTrigger("bbb")
  .timeBased()
  .everyMinutes(1)
  .create()
}

function a1() {
  // runs the trigger 
  var date = new Date()
  var day = date.getDay() // 0 represents sunday
  if (day >= 1 && day <= 5) {
    ScriptApp.newTrigger("b1")
    .timeBased()
    .everyDays(1)
    .atHour(08) // runs within an interval of +/- 15 minutes between 8am to 9am
    .inTimezone("Asia/Kolkata")
  }
}


const b1= () => {
  var milliseconds = calculateRemainingTime()
  ScriptApp.newTrigger("Trigger")
  .timeBased()
  .after(milliseconds)
  .create()
}
