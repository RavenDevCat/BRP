import { useSyncExternalStore } from "react";
import { getSubmissionLabel, subscribeSubmissionLabel } from "@/lib/api";
import { useT } from "@/lib/i18n/context";

export function SubmissionDestination(){
    const t=useT(),name=useSyncExternalStore(subscribeSubmissionLabel,getSubmissionLabel,()=>"");
    return name?<p aria-live="polite" className="min-w-0 self-center break-words text-xs text-muted-foreground">{t("School workspace")}: <strong>{name}</strong></p>:null;
}
