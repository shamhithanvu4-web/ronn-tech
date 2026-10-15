// synthetic test fixture
const { cexExports } = require('../helper/cex')
const bitcoinAddressBook = require('../helper/bitcoin-book/index.js')

const config = {
  bitcoin: { owners: bitcoinAddressBook.zetaBook },
  bsc: { owners: ['0x00000000000000000000000000000000000000d4'] },
}

module.exports = cexExports(config)
