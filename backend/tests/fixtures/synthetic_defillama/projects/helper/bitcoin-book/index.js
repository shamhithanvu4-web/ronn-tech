// synthetic test fixture
const imports = [
  ["zetaBook", "./zeta.js"],
];
const lonely = ["bc1qc0pu8s7rc0pu8s7rc0pu8s7rc0pu8s7rfdgfg5"];

module.exports = {
  lonely,
  acmeBook: [
    "bc1qk2et9v4jk2et9v4jk2et9v4jk2et9v4jhzxjr8",
    "bc1qnotvalid",
  ],
}

for (const [k, f] of imports) module.exports[k] = require(f)
