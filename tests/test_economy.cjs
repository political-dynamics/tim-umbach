const assert=require('node:assert/strict');
const data=require('../data/economy-model.json');
const {simulate}=require('../assets/economy-engine.js');
assert.equal(Object.keys(data.sectors).length,11);assert.equal(data.regions.length,16);
for(const m of Object.values(data.sectors)) {
  assert.ok(m.nobs>80);assert.ok(m.stable);assert.ok(m.capital.value.length>20);
  for(const mode of ['var','synthesis']) for(let shock=0;shock<4;shock++) {
    for(const size of [-3,0,3]) {
      const path=simulate(m,shock,size,mode);
      assert.equal(path.length,25);
      for(const row of path) for(const [key,value] of Object.entries(row)) {
        if(value!==null) assert.ok(Number.isFinite(value),`${m.name} ${mode} ${key}`);
        if(size===0&&key!=='quarter'&&value!==null)assert.equal(Math.abs(value),0);
      }
      assert.ok(path.every(r=>r.exchange===0));
      if(mode==='var')assert.ok(Math.abs(path[0][m.variables[shock]]-100*Math.expm1(size/100))<1e-8);
    }
  }
}
for(const r of data.regions) assert.ok(Math.abs(Object.values(r.weights).reduce((a,b)=>a+b,0)-1)<1e-10);
assert.throws(()=>simulate(data.sectors.TOTAL,0,Infinity));
console.log('Sector coverage, zero shocks, impulse normalization, finite scenarios and regional weights passed.');
