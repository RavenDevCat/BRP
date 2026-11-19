import {useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {Check,ChevronLeft,ChevronRight,Eye,Pencil} from "lucide-react";
import {Button} from "@/components/ui/button";
import {useT} from "@/lib/i18n/context";
import {adminHistoryEvents,confirmAdoption,getAdoptionInventory,previewAdoption,renameAdminTask,
    type AdoptionObject,type AdoptionPreview} from "@/lib/api";

type Props={actor:string;schools:{id:string;name:string}[]};
const field="h-9 max-w-full rounded border border-border bg-surface px-2 text-sm";
const secondary="break-all text-xs text-muted-foreground";
const navStyle="border-b-2 px-3 py-2 text-sm ";
const reason:Record<string,string>={selected:"Selected",pair:"Paired schedule",folder:"Folder contents",derived:"Derived result"};

export function HistoryAdministration({actor,schools}:Props){
    const t=useT(),client=useQueryClient();
    const inventory=useQuery({queryKey:["history-adoption",actor],queryFn:getAdoptionInventory});
    const [mode,setMode]=useState("Assign school"),[search,setSearch]=useState(""),[page,setPage]=useState(0);
    const [ids,setIds]=useState<string[]>([]),[target,setTarget]=useState("");
    const [plan,setPlan]=useState<AdoptionPreview|null>(null),[ack,setAck]=useState(false);
    const [edit,setEdit]=useState<AdoptionObject|null>(null),[name,setName]=useState("");
    const [busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
    const clear=()=>{setPlan(null);setAck(false);setError("");setNotice("")};
    const run=async(fn:()=>Promise<void>)=>{setBusy(true);setError("");setNotice("");try{await fn()}catch(e){
        setError(e instanceof Error?e.message:"Request failed");setPlan(null);setAck(false);
    }finally{setBusy(false)}};
    const refresh=async()=>{await Promise.all([inventory.refetch(),client.invalidateQueries({queryKey:["admin-history-events"]}),
        client.invalidateQueries({queryKey:["directory-audit-access"]}),client.invalidateQueries({queryKey:["school-directory"]})])};
    const adopt=mode==="Assign school";
    const items=(inventory.data?.items??[]).filter(r=>(adopt?!r.school:r.kind!=="folder")&&
        (r.ref+" "+r.title+" "+r.owner+" "+r.scope).toLowerCase().includes(search.trim().toLowerCase()));
    const p=Math.min(page,Math.max(0,Math.ceil(items.length/12)-1));
    const names=new Map(schools.map(s=>[s.id,s.name]));
    return <section className="min-w-0 space-y-3">
        <div className="flex flex-wrap border-b border-border">{["Assign school","Rename task"].map(k=>
            <button key={k} type="button" disabled={busy} aria-pressed={mode===k} className={navStyle+(mode===k?"border-primary font-semibold":"border-transparent")}
                onClick={()=>{setMode(k);clear();setEdit(null);setPage(0)}}>{t(k)}</button>)}</div>
        <div className="flex flex-wrap items-end gap-3">
            <label className="grid gap-1 text-xs">{t("Find history")}<input className={field} value={search} onChange={e=>{setSearch(e.target.value);setPage(0)}}/></label>
            {adopt&&<label className="grid gap-1 text-xs">{t("Target school")}<select className={field} value={target} disabled={busy} onChange={e=>{setTarget(e.target.value);clear()}}>
                <option value="">{t("Select school")}</option>{schools.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>}
        </div>
        {inventory.isError?<Button onClick={()=>inventory.refetch()}>{t("Retry")}</Button>:inventory.isPending?<p>{t("Loading…")}</p>:
        <ul className="divide-y divide-border">{items.slice(p*12,p*12+12).map(r=>
            <li key={r.ref} className="flex min-w-0 items-center gap-2 py-2">
                {adopt&&<input aria-label={r.ref} type="checkbox" disabled={busy} checked={ids.includes(r.ref)} onChange={e=>{
                    const on=e.target.checked;setIds(prev=>on?[...prev,r.ref]:prev.filter(i=>i!==r.ref));clear()}}/>}
                <div className="min-w-0 flex-1"><strong className="break-all text-sm">{r.title}</strong>
                    <div className={secondary}>{r.ref} · {r.owner} · {r.scope}{r.school?" · "+(names.get(r.school)??r.school):""}</div></div>
                {!adopt&&<button type="button" aria-label={t("Rename task")+": "+r.ref} title={t("Rename task")} className={field} disabled={busy}
                    onClick={()=>{clear();setEdit(r);setName(r.title)}}><Pencil size={15}/></button>}
            </li>)}</ul>}
        {!inventory.isPending&&!items.length&&<p className={secondary}>{t("No matching history")}</p>}
        <div className="flex flex-wrap items-center gap-2 text-xs"><button aria-label={t("Previous")} className={field} disabled={!p} onClick={()=>setPage(p-1)}><ChevronLeft size={15}/></button>
            {p+1} / {Math.max(1,Math.ceil(items.length/12))}
            <button aria-label={t("Next")} className={field} disabled={(p+1)*12>=items.length} onClick={()=>setPage(p+1)}><ChevronRight size={15}/></button>
            {adopt&&<Button icon={<Eye size={15}/>} disabled={busy||!target||!ids.length} onClick={()=>run(async()=>{setAck(false);setPlan(await previewAdoption(target,ids))})}>
                {t("Preview access")} ({ids.length})</Button>}</div>
        {adopt&&plan&&<div className="space-y-3 border-t border-border pt-3">
            <h3 className="font-semibold">{t("Access preview")} · {plan.school_name} · {plan.objects.length}</h3>
            <p className={secondary}>{t("Preview expires")}: {new Date(plan.expires*1000).toLocaleTimeString()}</p>
            <ul className="max-h-52 overflow-auto">{plan.objects.map(r=><li key={r.ref} className="break-all py-1 text-sm">{r.title} · {t(reason[r.reason]??r.reason)}
                <div className={secondary}>{r.ref}</div></li>)}</ul>
            {!plan.users.length&&<p className="text-sm">{t("No known user visibility changes")}</p>}
            {plan.users.map(u=><div className="border-t border-border py-1 text-sm" key={u.email}><strong className="break-all">{u.email}</strong>
                {([["Gains access",u.gained],["Loses current access",u.lost],["Excluded versus legacy access",u.legacy_lost]] as const)
                    .filter(([,v])=>v.length).map(([label,v])=><details key={label}><summary>{t(label)}: {v.length}</summary>
                    <ul>{v.map(k=><li key={k} className={secondary}>{k}</li>)}</ul></details>)}</div>)}
            {plan.open_legacy&&<p className="text-sm text-amber-800">{t("Legacy sharing also covered signed-in accounts outside the known directory.")}</p>}
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={ack} disabled={busy} onChange={e=>setAck(e.target.checked)}/>{t("I reviewed the objects and access changes")}</label>
            <Button icon={<Check size={15}/>} disabled={busy||!ack} onClick={()=>run(async()=>{
                const result=await confirmAdoption(plan.token);setPlan(null);setIds([]);setAck(false);setNotice(t("Assigned objects")+": "+result.count);await refresh();
            })}>{t("Confirm assignment")}</Button>
        </div>}
        {!adopt&&edit&&<form className="flex flex-wrap items-end gap-2 border-t border-border pt-3" onSubmit={e=>{e.preventDefault();void run(async()=>{
            const result=await renameAdminTask(edit.ref,edit.title,name);setEdit(null);setNotice(t("Renamed tasks")+": "+result.count);await refresh()})}}>
            <div className="w-full break-all text-xs">{edit.ref} · {edit.owner}</div>
            {edit.paired&&<p className="w-full text-sm">{t("Both morning and return tasks will be renamed.")}</p>}
            <label className="grid gap-1 text-xs">{t("New task name")}<input className={field} required maxLength={80} value={name} onChange={e=>setName(e.target.value)}/></label>
            <Button type="submit" disabled={busy||!name.trim()||name.trim()===edit.title}>{t("Save")}</Button></form>}
        {error&&<p role="alert" className="break-words text-sm text-red-700">{t(error)}</p>}
        {notice&&<p role="status" className="text-sm">{notice}</p>}
    </section>;
}

export function HistoryChanges({actor,schools}:Props){
    const t=useT(),[cursors,setCursors]=useState([0]),before=cursors[cursors.length-1];
    const query=useQuery({queryKey:["admin-history-events",actor,before],queryFn:()=>adminHistoryEvents(before)});
    const rows=query.data?.items??[],names=new Map(schools.map(s=>[s.id,s.name]));
    return <section className="space-y-2"><h3 className="font-semibold">{t("History changes")}</h3>
        {query.isError?<Button onClick={()=>query.refetch()}>{t("Retry")}</Button>:query.isPending?<p>{t("Loading…")}</p>:
            !rows.length?<p className={secondary}>{t("No changes recorded")}</p>:rows.map(r=><div key={r.seq} className="border-b border-border py-2 text-sm">
                <div className="break-all"><strong>{t(r.action==="rename"?"Rename task":"Assign school")}</strong> · {r.before_value??t("Unassigned")} → {r.action==="adopt"?names.get(r.after_value)??r.after_value:r.after_value}</div>
                <div className={secondary}>{r.ref} · {t("Owner")}: {r.owner}</div><div className={secondary}>{t("Changed by")}: {r.actor} · {new Date(r.at).toLocaleString()} · {r.batch}</div>
            </div>)}
        <button aria-label={t("Previous")} className={field} disabled={cursors.length<2} onClick={()=>setCursors(c=>c.slice(0,-1))}><ChevronLeft size={15}/></button>
        <button aria-label={t("Next")} className={field} disabled={rows.length<50} onClick={()=>setCursors(c=>[...c,rows[rows.length-1].seq])}><ChevronRight size={15}/></button>
    </section>;
}
