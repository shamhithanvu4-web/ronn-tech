// synthetic test fixture: owners loaded at runtime, so nothing static to extract
const axios = require('axios')
async function tvl(api) {
  const { data } = await axios.get('https://example.invalid/por.csv')
  return api.sumTokens({ owners: data.split(',') })
}
module.exports = { ethereum: { tvl } }
