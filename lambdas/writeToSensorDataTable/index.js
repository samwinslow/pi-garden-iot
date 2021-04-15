// Lambda for node14.x
const AWS = require('aws-sdk')
const uuid = require('uuid')
AWS.config.update({region: 'us-east-1'})

// Create the DynamoDB service object
const ddb = new AWS.DynamoDB({apiVersion: '2012-08-10'})
const TableName = 'SensorData'

exports.handler = async (payload) => {
    const params = {
      TableName,
      Item: {
          id: {
            S: uuid.v4()
          },
          temperature: {
            N: payload.temperature.toString()
          },
          capacitance: {
            N: payload.capacitance.toString()
          },
          status: {
            M: {
              lightRelay: {
                N: payload.status.lightRelay.toString()
              },
              pumpRelay: {
                N: payload.status.pumpRelay.toString()
              },
            }
          }
      }
    }
    try {
      const data = await ddb.putItem(params).promise()
      return {
        statusCode: 200,
        body: JSON.stringify({
            status: "success",
            data
        }),
      }
    } catch (error) {
      return {
        statusCode: error.statusCode || 500,
        body: JSON.stringify({
            status: "error",
            error
        }),
      }
    }
}
