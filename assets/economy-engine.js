/* Pure scenario engine, shared by the browser and Node tests. */
(function (root) {
  function simulate(model, shock, size, mode='var', inertia=.65, accelerator=2) {
    if (!Number.isFinite(size) || Math.abs(size)>3 || shock<0 || shock>3) throw Error('Invalid shock');
    const cumulative=[0,0,0,0]; let capital=0, price=0, nominalWage=0;
    return model.irf.map((matrix,h)=>{
      matrix.forEach((row,i)=>{ cumulative[i]+=row[shock]*size/100; });
      const levels=cumulative.map(x=>100*Math.expm1(x));
      if(mode==='synthesis') {
        // Deviations around a common baseline; calibrated capital accumulation.
        capital=(1-.0125)*capital+.015*accelerator*(h ? 100*Math.expm1(cumulative[0]-matrix[0][shock]*size/100) : 0);
        price=inertia*price+(1-inertia)*levels[3];
        nominalWage=inertia*nominalWage+(1-inertia)*(100*((1+levels[2]/100)*(1+levels[3]/100)-1));
        levels[1]=100*Math.expm1((Math.log1p(levels[0]/100)-model.capital_share*Math.log1p(capital/100))/(1-model.capital_share));
        levels[2]=100*((1+nominalWage/100)/(1+price/100)-1); levels[3]=price;
      }
      return {quarter:h,output:levels[0],employment:levels[1],wages:levels[2],prices:levels[3],capital:mode==='synthesis'?capital:null,exchange:0,realExchange:levels[3]};
    });
  }
  root.EconomyEngine={simulate};
  if(typeof module!=='undefined') module.exports={simulate};
})(globalThis);
