const getDate= () => {
  const date = new Date()
  return date
}

const convertTimeToMilliseconds= (timeList) => {
  // timeList should be a list of [hours, minutes, seconds]
  var milliseconds = 0
  if (timeList) {
    milliseconds += timeList[0]*60*60*1000
    if (timeList.length == 2) {
      milliseconds += timeList[1]*60*1000
      if (timeList.length == 3) {
        milliseconds += timeList[2]*1000
      }
    }
  }
  return milliseconds
}

const calculateCurrentTime = () => {
  const date = getDate()
  Logger.log(date)
  // calculate minutes
  var hours = date.getHours()
  var minutes = date.getMinutes()
  var flag = false
  if (convertTimeToMilliseconds([hours, minutes]) == convertTimeToMilliseconds([14, 30])) {
    flag = true
  }
  var returnValues = [] // 0th: Date, 1st: Hours, 2nd: Minutes
  var carry = 0
  if (minutes < 30) {
    minutes += 30
  } else if (minutes == 30) {
    minutes = 0
    carry += 1
  } else if (minutes > 30) {
    minutes -= 30
    carry += 1
  }

  // calculate hours
  if (hours < 15) {
    hours += (9 + carry)
    if (hours >= 24) {
      hours = 0
    }
  } else if (hours == 15) {
    hours += (0 + carry)
  } else if (hours > 15) {
   hours = ((hours + 9) - 24) + carry
  }
  Logger.log("flag : " + flag)
  if (flag) {
    returnValues.push(date.getDate() + 1)
  } else {
    returnValues.push(date.getDate())
  }
  returnValues.push(...[hours, minutes])
  Logger.log(returnValues)
  return returnValues
}

const calculateRemainingTime = () => {
  var currentTime = calculateCurrentTime()
  Logger.log(currentTime)
  var remainingTime = [0, 0] // hours, minutes
  var carry = 0
  // calculate minutes
  if (currentTime[2] <= 15) {
    remainingTime[1] = 15 - currentTime[2]
  } else {
    remainingTime[1] = 75 - currentTime[2]
    carry = 1
  }
  
  //calculate hours
  if (currentTime[1] <= 9) {
    remainingTime[0] = 9 - currentTime[1]
  } else {
    remainingTime[0] = (24 - currentTime[1]) + 9
  }
  remainingTime[0] -= carry
  
  Logger.log(remainingTime)
  Logger.log(convertTimeToMilliseconds(remainingTime))
}
