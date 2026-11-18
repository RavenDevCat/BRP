const {chromium}=require("playwright");
const fs=require("node:fs/promises"), path=require("node:path"), assert=require("node:assert/strict");
(async()=>{
 const browser=await chromium.launch({channel:process.env.BRP_QA_BROWSER_CHANNEL || "msedge",headless:true});
 try {
 for(const [view,title] of [["/fleet","Fleet Planner History"],["/distance","Reference Distance History"],["/insert-advisor","Route Insert History"]]){
 const page=await browser.newPage({viewport:{width:1440,height:920}});
 page.setDefaultTimeout(6000);
 const errors=[];page.on("pageerror",e=>errors.push(e.message));page.on("dialog",d=>d.accept());
 const fixture=JSON.parse(await fs.readFile(process.env.BRP_QA_FLEET_FIXTURE,"utf8"));
 let items=["A","B"], fetched=0, deletes=[], exports=0, release, started;
 const slow=new Promise(r=>release=r), pending=new Promise(r=>started=r);
 const row=id=>({run_id:id,title:"QA "+id,summary:{},scenario:{},created_at:"2026-11-18T02:01:00Z",owner_email:"qa@example.invalid",shared_with_all:false});
 const root=process.env.BRP_QA_WEB_DIST;
 await page.route("**/*",async route=>{
  const url=new URL(route.request().url()), p=url.pathname;
  if(url.host!=="qa.invalid")return route.abort();
  if(p.startsWith("/assets/")) return route.fulfill({path:path.join(root,p)});
  if(!p.startsWith("/api/"))return route.fulfill({path:path.join(root,"index.html"),contentType:"text/html"});
  const send=(v,s=200)=>route.fulfill({json:v,status:s});
  if(/history\/(A|B)$/.test(p)){
    const id=p.at(-1);
    if(route.request().method()==="DELETE"){deletes.push(id);if(id==="B")return send({error:"injected failure"},503);items=items.filter(x=>x!==id);return send({run_id:id});}
    if(id==="A"){started();await slow;}
    return send({...row(id),...(view==="/fleet"?{preview_result:{summary:{},assumptions:{},mix_summary:{vehicle_mix:{},selections:[]},recommendations:[],decision_details:[],catalog:[]},global_plan_result:{...fixture.plan,map_data:undefined}}:{}),route_insert_result:{status:"completed",proposal_status:"completed",scenarios:[],summary:{},new_stops:[],proposals:[],rows:[]}});
  }
  if(p.endsWith("/fleet-planner/workbook")){exports++;const expected=JSON.parse(JSON.stringify(fixture.plan.routes,(k,v)=>["geometry","geometry_segments","polyline","leg_details"].includes(k)?undefined:v));assert.deepEqual(route.request().postDataJSON().routes,expected);return send(fixture.export);}
  if(p.endsWith("history")){fetched++;return send({jobs:items.map(row)});}
  if(p.includes("history-group"))return send({groups:[]});
  if(p.endsWith("/me"))return send({email:"qa@example.invalid",is_admin:true});
  if(p.includes("vehicle-catalog"))return send({vehicles:[],vehicle_catalog:[],catalog:[]});
  if(p.includes("capabilities"))return send({supported_sources:[],candidate_checks:[]});
  return send({status:"ok",groups:[],jobs:[],reviews:[],risk_routes:[],items:[],routes:[]});
 });
 try{
 await page.goto("http://qa.invalid"+view);
 const expand=page.getByRole("button",{name:"Open "+title,exact:true});
 const ensureOpen=async()=>{if(await expand.isVisible())await expand.click();};
 await expand.waitFor();
 await ensureOpen();
 const item=id=>page.getByRole("button",{name:"QA "+id,exact:true});
 await item("A").first().click();await pending;await ensureOpen();await item("B").first().click();
 await ensureOpen();
 const active=page.locator("div.border-primary.bg-primary").filter({has:item("B")});
 await active.first().waitFor();release();
 await page.waitForTimeout(250); assert(await active.first().isVisible());
 if(view==="/fleet"){
   assert.equal(exports,0);
   await page.getByRole("button",{name:/^Plan.*1 route/}).first().click();
   for(let i=0;i<2;i++){
     const download=page.waitForEvent("download");
     await page.getByRole("button",{name:"Download workbook",exact:true}).click();
     const file=await download;assert.equal(file.suggestedFilename(),"qa.xlsx");
     assert.deepEqual(await fs.readFile(await file.path()),Buffer.from(fixture.export.workbook_base64,"base64"));
   }
   assert.equal(exports,1);console.log("Fleet export: zero eager calls, two identical downloads, one render request");
 }
 await ensureOpen();
 await page.getByRole("button",{name:"Select",exact:true}).click();
 for(const id of ["A","B"])await page.getByRole("checkbox",{name:"Select "+id,exact:true}).check();
 await page.getByRole("button",{name:"Delete selected (2)",exact:true}).click();
 await page.getByText("Failed to delete 1 history record(s).",{exact:false}).first().waitFor();
 assert.equal(await item("A").count(),0);assert(await item("B").first().isVisible());
 assert.deepEqual(deletes,["A","B"]);assert(fetched>=2);assert.deepEqual(errors,[]);
 console.log(view,"late response ignored; partial delete refreshed; remaining item displayed");
 }catch(err){const show=page.getByRole("button",{name:"Show Error"});if(await show.isVisible())await show.click();console.error(view,errors,(await page.locator("body").innerText()).slice(-1500));throw err;}
 finally{release();await page.close();}
 }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
