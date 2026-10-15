const { cexExports } = require('../projects/helper/cex')

// synthetic test fixture (not real exchange data)
const configs = {
  'acmex': {
    methodology: 'Synthetic methodology text',
    bitcoin: "acmeBook",
    ethereum: {
      owners: [
        '0x00000000000000000000000000000000000000A1', // hot
        // '0x0000000000000000000000000000000000000099', commented out
        ADDRESSES.ethereum.USDT,
      ]
    },
    tron: { owners: ['TQhqRHgEonKEYqudomS8243o3bejg8dt1d', 'TNotAValidTronAddressXXXXXXXXXXXXX'] },
    ripple: { owners: ['rSyntheticNotInScope'] },
  },
  'beta-cex': {
    ethereum: { owners: sharedOwners },
  },
}

const sharedOwners = [
  '0x00000000000000000000000000000000000000b2',
  ...moreOwners,
]
const moreOwners = ['0x00000000000000000000000000000000000000b3']

const fileConfigs = [
  'gamma',
  'dynamic-ex',
]

for (const name of fileConfigs) configs[name] = require(`./${name}.js`)
module.exports = configs
