import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { UserRoundPlus, X } from "lucide-react";
import { applyForSchool, decideSchoolRequest, getSchoolRequests, type ApplicationRole } from "@/lib/api";
import { useT } from "@/lib/i18n/context";
import { Button } from "@/components/ui/button";

type Props={email:string;memberships:string[];data?:Awaited<ReturnType<typeof getSchoolRequests>>;loadError:Error|null};
const field="rounded border border-border bg-surface px-2 py-1 text-sm";
export function ApplicationMenu({email,memberships,data,loadError}:Props){
    const t=useT(), dialog=useRef<HTMLDialogElement>(null), cache=useQueryClient();
    const [choice,setChoice]=useState(""),[role,setRole]=useState<ApplicationRole>("operator");
    const [grants,setGrants]=useState<Record<string,ApplicationRole>>({});
    const action=useMutation({mutationFn:(fn:()=>Promise<unknown>)=>fn(),onSuccess:async()=>{
        await Promise.all(["school-requests","school-choices","jobs"].map(key=>cache.invalidateQueries({queryKey:[key]})));
        setChoice("");
    }});
    const rows=data?.applications??[], own=rows.filter(r=>r.email===email), review=rows.filter(r=>r.email!==email && r.status==="pending");
    const available=(data?.schools??[]).filter(s=>!memberships.includes(s.id)&&!own.some(r=>r.school===s.id&&r.status==="pending"));
    const roles=<><option value="viewer">{t("Viewer")}</option><option value="operator">{t("Operator")}</option></>;
    return <><Button type="button" variant="secondary" title={t("School requests")} aria-label={t("School requests")} onClick={()=>dialog.current?.showModal()}>
        <UserRoundPlus size={16}/>{!memberships.length&&t("Apply to school")}{review.length>0&&review.length}
    </Button>
    <dialog ref={dialog} className="m-auto w-[min(36rem,94vw)] max-h-[85vh] overflow-auto rounded-lg border border-border bg-surface p-5 text-foreground backdrop:bg-black/30" aria-label={t("School requests")}>
        <header className="mb-4 flex items-center justify-between"><h2 className="font-semibold">{t("School requests")}</h2>
            <button type="button" aria-label={t("Close")} onClick={()=>dialog.current?.close()}><X size={18}/></button></header>
        {(loadError||action.error)&&<p role="alert" className="mb-2 text-sm text-red-700">{(loadError||action.error)?.message}</p>}
        {available.length>0&&<form className="mb-4 flex flex-wrap items-end gap-2" onSubmit={e=>{e.preventDefault();action.mutate(()=>applyForSchool(choice,role))}}>
            <label className="grid text-xs">{t("School workspace")}<select className={field} required value={choice} onChange={e=>setChoice(e.target.value)}>
                <option value="">{t("Select school")}</option>{available.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
            <label className="grid text-xs">{t("Role")}<select className={field} value={role} onChange={e=>setRole(e.target.value as ApplicationRole)}>{roles}</select></label>
            <Button disabled={!choice||action.isPending}>{t("Apply to school")}</Button></form>}
        {own.map(r=><div key={r.id} className="flex flex-wrap items-center justify-between gap-2 border-t border-border py-2 text-sm">
            <span>{r.name} · {t(r.status)} · {t((r.granted??r.requested)==="operator"?"Operator":"Viewer")}</span>
            {r.status==="pending"&&<button disabled={action.isPending} onClick={()=>action.mutate(()=>decideSchoolRequest(r.id,"withdrawn"))}>{t("Withdraw")}</button>}</div>)}
        {review.length>0&&<h3 className="mt-3 text-sm font-semibold">{t("Awaiting approval")}</h3>}
        {review.map(r=><div key={r.id} className="flex flex-wrap items-center gap-2 border-t border-border py-2 text-sm"><span className="grow break-all">{r.email} · {r.name}</span>
            <select className={field} aria-label={t("Role")} value={grants[r.id]??r.requested} onChange={e=>setGrants({...grants,[r.id]:e.target.value as ApplicationRole})}>{roles}</select>
            <Button disabled={action.isPending} onClick={()=>action.mutate(()=>decideSchoolRequest(r.id,"approved",grants[r.id]??r.requested))}>{t("Approve")}</Button>
            <button disabled={action.isPending} onClick={()=>action.mutate(()=>decideSchoolRequest(r.id,"rejected"))}>{t("Reject")}</button></div>)}
    </dialog></>;
}
