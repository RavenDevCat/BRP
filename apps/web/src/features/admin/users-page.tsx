import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, ChevronLeft, ChevronRight, Plus, RefreshCw, Search, UserMinus, UsersRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HistoryAdministration, HistoryChanges } from "./history-admin-panel";
import { useT } from "@/lib/i18n/context";
import { changeSchoolMember, createSchool, getCurrentUser, getSchoolDirectory, getUserAuditAccess,
    type DirectoryUser, type SchoolDirectory, type SchoolRole } from "@/lib/api";

const roles: SchoolRole[] = ["school_admin","operator","viewer"];
const labels = {school_admin:"School administrator",operator:"Operator",viewer:"Viewer"};
const field = "min-h-9 max-w-full rounded border border-border bg-surface px-2 text-sm";
const panel = "min-w-0 border-t border-border py-4";
const chip = "inline-block rounded border border-border bg-muted px-2 py-0.5 text-xs";
const scopes: Record<string,string> = {route_audit:"Route Audit",fleet_planner:"Fleet Planner",
    distance_reference:"Reference Distance",distance_route_cost:"Route Cost",distance_direct_school:"Direct-to-School",
    route_insert_advisor:"Route Insert Advisor"};

export function UsersPage(){
    const t=useT(), me=useQuery({queryKey:["me"],queryFn:getCurrentUser});
    if(me.isPending)return <p>{t("Loading…")}</p>;
    if(!me.data?.is_admin || !me.data.school_directory_enabled)return <p>{t("Access unavailable")}</p>;
    return <DirectoryConsole key={me.data.email} actor={me.data.email} scoped={!!me.data.school_scopes_enabled}/>;
}

function DirectoryConsole({actor,scoped}:{actor:string;scoped:boolean}){
    const t=useT(), client=useQueryClient(), key=["school-directory",actor];
    const query=useQuery({queryKey:key,queryFn:getSchoolDirectory,staleTime:0});
    const [tab,setTab]=useState("Users"),[newSchool,setNewSchool]=useState(false);
    const [name,setName]=useState(""),[manager,setManager]=useState(""),[school,setSchool]=useState("");
    const [selectedEmail,setEmail]=useState(""),[search,setSearch]=useState("");
    const mutation=useMutation({mutationFn:(task:()=>Promise<unknown>)=>task(),onSettled:()=>client.invalidateQueries({queryKey:key})});
    const data=query.data;
    if(!data)return <div role="status">{query.isError?<Button onClick={()=>query.refetch()}>{t("Retry")}</Button>:t("Loading…")}</div>;
    const users=new Map<string,DirectoryUser>(data.users.map(u=>[u.email,u]));
    for(const m of data.members)if(!users.has(m.email))users.set(m.email,{email:m.email,system_admin:false,owned_tasks:0,folder_roles:[]});
    const filtered=[...users.values()].filter(u=>u.email.toLowerCase().includes(search.trim().toLowerCase())).sort((a,b)=>a.email.localeCompare(b.email));
    const user=filtered.find(u=>u.email===selectedEmail)??filtered[0];
    const active=school?data.schools.find(s=>s.id===school):data.schools[0];
    const schools=new Map(data.schools.map(s=>[s.id,s.name]));
    const busy=mutation.isPending;
    const create=(e:FormEvent)=>{e.preventDefault();mutation.mutate(async()=>{
        const x=await createSchool(name,manager);
        await client.invalidateQueries({queryKey:key});
        setSchool(x.id);setName("");setManager("");setNewSchool(false);setTab("School workspaces");
    })};
    return <main className="mx-auto max-w-6xl space-y-4 rounded border border-border bg-surface p-4 sm:p-6">
        <header className="flex flex-wrap items-center justify-between gap-3">
            <h1 className="flex items-center gap-2 text-xl font-semibold"><UsersRound aria-hidden size={20}/>{t("Users & access")}</h1>
            <div className="flex items-center gap-2">
                <button type="button" title={t("Refresh")} aria-label={t("Refresh")} onClick={()=>query.refetch()} className={field}><RefreshCw size={15}/></button>
                <Button icon={<Plus size={16}/>} onClick={()=>setNewSchool(!newSchool)}>{t("New school")}</Button>
            </div>
        </header>
        {newSchool&&<form onSubmit={create} className="flex flex-wrap items-end gap-3 border-y border-border py-3">
            <label className="grid gap-1 text-xs">{t("School name")}<input className={field} value={name} maxLength={100} required onChange={e=>setName(e.target.value)}/></label>
            <label className="grid gap-1 text-xs">{t("School administrator")}<input className={field} type="email" required value={manager} onChange={e=>setManager(e.target.value)}/></label>
            <Button type="submit" disabled={busy}>{t("Create")}</Button>
        </form>}
        <nav aria-label={t("Users & access")} className="flex flex-wrap gap-1 border-b border-border">
            {["Users","School workspaces",...(scoped?["History administration"]:[]),"Change log"].map(k=><button type="button" key={k} aria-pressed={tab===k}
                className={"border-b-2 px-3 py-2 text-sm "+(tab===k?"border-primary font-semibold text-primary":"border-transparent text-muted-foreground")}
                onClick={()=>setTab(k)}>{t(k)}</button>)}
        </nav>
        {mutation.isError&&<p role="alert" className="text-sm text-red-700">{t(mutation.error.message)}</p>}
        {query.isError&&<p role="alert" className="text-sm text-red-700">{t("Directory could not be refreshed")}</p>}
        {tab==="Users"&&<div className="grid gap-5 md:grid-cols-[minmax(200px,1fr)_2fr]">
            <div className="min-w-0"><label className="relative block"><Search size={15} aria-hidden className="absolute left-2 top-3"/>
                <input className={field+" w-full pl-8"} aria-label={t("Search users")} placeholder={t("Search users")} value={search} onChange={e=>setSearch(e.target.value)}/></label>
                <ul className="mt-2 max-h-96 overflow-auto">{filtered.map(u=><li key={u.email}><button type="button"
                    className={"w-full break-all rounded px-2 py-2 text-left text-sm "+(user?.email===u.email?"bg-muted font-semibold":"")}
                    onClick={()=>setEmail(u.email)}>{u.email}</button></li>)}</ul></div>
            {!user?<p>{t("No matching users")}</p>:<section className="min-w-0 space-y-3">
                <h2 className="break-all font-semibold">{user.email}</h2>
                {user.system_admin&&<span className={chip}>{t("System administrator")}</span>}
                <h3 className="text-sm font-semibold">{t("School memberships")}</h3>
                <ul className="space-y-1">{data.members.filter(m=>m.email===user.email).map(m=><li key={m.school_id}>
                    <button className="text-sm text-primary underline" onClick={()=>{setSchool(m.school_id);setTab("School workspaces")}}>{schools.get(m.school_id)}</button>
                    {" "}<span className={chip}>{t(labels[m.role])}</span></li>)}</ul>
                {!data.members.some(m=>m.email===user.email)&&<p className="text-sm text-muted-foreground">{t("No school membership")}</p>}
                <section className={panel}><h3 className="text-sm font-semibold">{t("Existing history access")}</h3>
                    <p className="my-2 text-sm">{t("Owned history records")}: {user.owned_tasks}</p>
                    <p className="my-2 text-sm">{t("Shared history records")}: {data.shared_records}</p>
                    {user.folder_roles.map((f,i)=><div key={i} className="flex flex-wrap items-center gap-2 border-t border-border py-2 text-sm">
                        <span className="break-all">{f.name}</span><span className="text-muted-foreground">{t(scopes[f.scope]??f.scope)}</span>
                        <span className={chip}>{t(f.role.charAt(0).toUpperCase()+f.role.slice(1))}</span></div>)}
                </section><UserAuditAccess key={user.email} email={user.email}/></section>}
        </div>}
        {tab==="School workspaces"&&!newSchool&&<section className="space-y-3">
            {active?<><label className="flex flex-wrap items-center gap-2 text-sm"><Building2 size={16}/>{t("School workspace")}
                <select className={field} value={active.id} onChange={e=>setSchool(e.target.value)}>{data.schools.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
                <SchoolMembers key={active.id} data={data} id={active.id} busy={busy} run={task=>mutation.mutate(task)}/></>:
                <p className="text-sm text-muted-foreground">{t("No school workspaces")}</p>}
            <p className="text-xs text-muted-foreground">{t(scoped?"Unassigned history requires an administrator's confirmed assignment.":"School memberships do not change existing task access.")}</p>
        </section>}
        {scoped&&tab==="History administration"&&<HistoryAdministration actor={actor} schools={data.schools}/>}
        {tab==="Change log"&&<section className="space-y-1">
            {scoped&&<><HistoryChanges actor={actor} schools={data.schools}/><h3 className="pt-5 text-sm font-semibold">{t("School memberships")}</h3></>}
            {!data.changes.length&&<p className="text-sm text-muted-foreground">{t("No changes recorded")}</p>}
            {data.changes.map(c=><div key={c.seq} className="grid gap-1 border-b border-border py-2 text-sm sm:grid-cols-3">
                <span>{schools.get(c.school_id)}<br/><small>{new Date(c.at).toLocaleString()}</small></span>
                <span className="break-all">{c.subject}<br/>{c.previous?t(labels[c.previous]):"∅"} → {c.current?t(labels[c.current]):"∅"}</span>
                <span className="break-all text-muted-foreground">{t("Changed by")}: {c.actor}</span></div>)}
        </section>}
    </main>;
}

function UserAuditAccess({email}:{email:string}){
    const t=useT(),[page,setPage]=useState(0);
    const q=useQuery({queryKey:["directory-audit-access",email,page],queryFn:()=>getUserAuditAccess(email,page)});
    return <section className={panel}><div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{t("Audit History")} {q.data?.total??""}</h3>
        <div className="flex gap-1"><button className={field} disabled={page===0} aria-label={t("Previous")} onClick={()=>setPage(page-1)}><ChevronLeft size={14}/></button>
        <button className={field} disabled={!q.data||(page+1)*12>=q.data.total} aria-label={t("Next")} onClick={()=>setPage(page+1)}><ChevronRight size={14}/></button></div></div>
        {q.isError?<Button onClick={()=>q.refetch()}>{t("Retry")}</Button>:q.isPending?<p>{t("Loading…")}</p>:
        q.data.items.map(r=><div key={r.id} className="flex flex-wrap items-center gap-2 border-b border-border py-2 text-sm">
            <span className="break-all">{r.title}</span>{[...(r.own?[t("Owner")]:[]),...(r.admin?[t("System administrator")]:[]),
            ...(r.shared?[t("Shared with all")]:[]),...r.folders].map((s,i)=><span key={i} className={chip}>{s}</span>)}</div>)}</section>;
}

function SchoolMembers({data,id,busy,run}:{data:SchoolDirectory;id:string;busy:boolean;run:(task:()=>Promise<unknown>)=>void}){
    const t=useT(),[email,setEmail]=useState(""),[role,setRole]=useState<SchoolRole>("viewer");
    const members=data.members.filter(m=>m.school_id===id);
    return <><form className="flex flex-wrap items-end gap-2" onSubmit={e=>{e.preventDefault();run(async()=>{
        await changeSchoolMember(id,email,role,null);setEmail("");
    })}}>
        <label className="grid gap-1 text-xs">{t("Member email")}<input type="email" required value={email} onChange={e=>setEmail(e.target.value)} className={field}/></label>
        <label className="grid gap-1 text-xs">{t("Role")}<select className={field} value={role} onChange={e=>setRole(e.target.value as SchoolRole)}>
            {roles.map(r=><option key={r} value={r}>{t(labels[r])}</option>)}</select></label>
        <Button type="submit" disabled={busy} icon={<Plus size={15}/>}>{t("Add member")}</Button>
    </form>
    <ul>{members.map(m=><li key={m.email} className="flex flex-wrap items-center gap-2 border-b border-border py-2">
        <span className="min-w-32 flex-1 break-all text-sm">{m.email}</span>
        <select className={field} disabled={busy} aria-label={t("Role")+": "+m.email} value={m.role}
            onChange={e=>{const next=e.target.value as SchoolRole;run(()=>changeSchoolMember(id,m.email,next,m.role))}}>
            {roles.map(r=><option key={r} value={r}>{t(labels[r])}</option>)}</select>
        <button disabled={busy} aria-label={t("Remove member")+": "+m.email} title={t("Remove member")} className={field}
            onClick={()=>{if(window.confirm(t("Remove member")+": "+m.email+"?"))run(()=>changeSchoolMember(id,m.email,null,m.role))}}><UserMinus size={16}/></button>
    </li>)}</ul></>;
}
