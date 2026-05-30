import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Calculator, FileSpreadsheet, Fuel, Loader2, MapPinned, Ruler, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  previewDistanceWorkbook,
  runCurrentPlanRouteCost,
  runReferenceDistanceCheck,
  type DistanceWorkbookPreview,
  type ReferenceDistanceResponse,
  type RouteCostResponse,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";

const fieldClassName =
  "h-9 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none transition focus:border-primary";
const textareaClassName =
  "min-h-20 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm outline-none transition focus:border-primary";
const routeCostProfiles = {
  china: {
    label: "China",
    defaultCity: "Shanghai",
    defaultCountry: "China",
    currencyCode: "CNY",
    currencyLabel: "RMB",
    dieselPrice: 8.402,
    dieselStep: 0.1,
  },
  korea: {
    label: "South Korea",
    defaultCity: "Seoul",
    defaultCountry: "South Korea",
    currencyCode: "KRW",
    currencyLabel: "KRW",
    dieselPrice: 2006.19,
    dieselStep: 10,
  },
};

export function DistanceCheckerPage() {
  const [activeTool, setActiveTool] = useState<"reference" | "route_cost">("reference");
  const [file, setFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState("");
  const [fileError, setFileError] = useState("");
  const [preview, setPreview] = useState<DistanceWorkbookPreview | null>(null);
  const [selectedSheet, setSelectedSheet] = useState("");
  const [routeColumn, setRouteColumn] = useState("");
  const [addressColumn, setAddressColumn] = useState("");
  const [sequenceColumn, setSequenceColumn] = useState("");
  const [busTypeColumn, setBusTypeColumn] = useState("");
  const [cityColumn, setCityColumn] = useState("");
  const [countryColumn, setCountryColumn] = useState("");
  const [originCountry, setOriginCountry] = useState("South Korea");
  const [originCity, setOriginCity] = useState("Seoul");
  const [originAddress, setOriginAddress] = useState("");
  const [distanceMode, setDistanceMode] = useState<"road" | "straight_line">("road");
  const [result, setResult] = useState<ReferenceDistanceResponse | null>(null);
  const [routeCostProfileKey, setRouteCostProfileKey] = useState<keyof typeof routeCostProfiles>("korea");
  const [routeDefaultCity, setRouteDefaultCity] = useState(routeCostProfiles.korea.defaultCity);
  const [routeDefaultCountry, setRouteDefaultCountry] = useState(routeCostProfiles.korea.defaultCountry);
  const [dieselPrice, setDieselPrice] = useState(routeCostProfiles.korea.dieselPrice);
  const [fuelEfficiency, setFuelEfficiency] = useState(3);
  const [routeCostResult, setRouteCostResult] = useState<RouteCostResponse | null>(null);

  const previewMutation = useMutation({
    mutationFn: async (sheetName?: string) => {
      if (!file || !fileBase64) {
        throw new Error("Select a workbook first.");
      }
      return previewDistanceWorkbook({
        file_name: file.name,
        file_base64: fileBase64,
        selected_sheet: sheetName || selectedSheet || undefined,
      });
    },
    onSuccess: (payload) => {
      setPreview(payload);
      setSelectedSheet(payload.selected_sheet);
      setRouteColumn(payload.suggested_columns.route || "");
      setAddressColumn(payload.suggested_columns.address || payload.columns[0] || "");
      setSequenceColumn(payload.suggested_columns.sequence || "");
      setBusTypeColumn(payload.suggested_columns.bus_type || "");
      setCityColumn(payload.suggested_columns.city || "");
      setCountryColumn(payload.suggested_columns.country || "");
      setResult(null);
      setRouteCostResult(null);
    },
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64 || !preview) {
        throw new Error("Preview a workbook first.");
      }
      return runReferenceDistanceCheck({
        file_name: file.name,
        file_base64: fileBase64,
        selected_sheet: selectedSheet,
        address_column: addressColumn,
        city_column: cityColumn || undefined,
        country_column: countryColumn || undefined,
        distance_mode: distanceMode,
        origin: {
          country: originCountry,
          city: originCity,
          address: originAddress,
        },
      });
    },
    onSuccess: (payload) => setResult(payload),
  });

  const routeCostMutation = useMutation({
    mutationFn: async () => {
      if (!file || !fileBase64 || !preview) {
        throw new Error("Preview a workbook first.");
      }
      const profile = routeCostProfiles[routeCostProfileKey];
      return runCurrentPlanRouteCost({
        file_name: file.name,
        file_base64: fileBase64,
        selected_sheet: selectedSheet,
        route_column: routeColumn,
        address_column: addressColumn,
        sequence_column: sequenceColumn || undefined,
        bus_type_column: busTypeColumn || undefined,
        city_column: cityColumn || undefined,
        country_column: countryColumn || undefined,
        default_city: routeDefaultCity,
        default_country: routeDefaultCountry,
        currency_code: profile.currencyCode,
        currency_label: profile.currencyLabel,
        diesel_price_per_liter: dieselPrice,
        fuel_efficiency_km_per_liter: fuelEfficiency,
      });
    },
    onSuccess: (payload) => setRouteCostResult(payload),
  });

  useEffect(() => {
    if (preview && selectedSheet && selectedSheet !== preview.selected_sheet) {
      previewMutation.mutate(selectedSheet);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSheet]);

  useEffect(() => {
    const profile = routeCostProfiles[routeCostProfileKey];
    setRouteDefaultCity(profile.defaultCity);
    setRouteDefaultCountry(profile.defaultCountry);
    setDieselPrice(profile.dieselPrice);
    setRouteCostResult(null);
  }, [routeCostProfileKey]);

  function clearDistanceResult() {
    setResult(null);
  }

  function clearRouteCostResult() {
    setRouteCostResult(null);
  }

  function handleSheetChange(nextSheet: string) {
    setSelectedSheet(nextSheet);
    setResult(null);
    setRouteCostResult(null);
  }

  function handleAddressColumnChange(nextColumn: string) {
    setAddressColumn(nextColumn);
    setResult(null);
    setRouteCostResult(null);
  }

  async function handleFileChange(nextFile: File | null) {
    setFile(nextFile);
    setPreview(null);
    setResult(null);
    setRouteCostResult(null);
    setFileError("");
    setFileBase64("");
    if (!nextFile) {
      return;
    }
    const suffix = nextFile.name.split(".").pop()?.toLowerCase();
    if (!suffix || !["xlsx", "xlsm"].includes(suffix)) {
      setFileError("Use an .xlsx or .xlsm workbook.");
      return;
    }
    try {
      setFileBase64(await fileToBase64(nextFile));
    } catch (error) {
      setFileError(error instanceof Error ? error.message : "Workbook could not be read.");
    }
  }

  const routeCostProfile = routeCostProfiles[routeCostProfileKey];
  const resultRows = result?.results || [];
  const resultColumns = useMemo(() => {
    const keys = new Set<string>();
    for (const row of resultRows.slice(0, 20)) {
      Object.keys(row).forEach((key) => keys.add(key));
    }
    const preferred = [
      "source_excel_row",
      "input_address",
      "status",
      "distance_km",
      "duration_min",
      "formatted_address",
      "warning",
    ];
    return [...preferred.filter((key) => keys.has(key)), ...[...keys].filter((key) => !preferred.includes(key)).slice(0, 6)];
  }, [resultRows]);

  const routeRows = routeCostResult?.route_results || [];
  const routeColumns = useMemo(
    () =>
      tableColumns(routeRows, [
        "route_id",
        "bus_type",
        "diesel_cost_status",
        "stops_in_file",
        "resolved_stops",
        "failed_stops",
        "drive_legs",
        "route_distance_km",
        "route_duration_min",
        "estimated_diesel_liters",
        "estimated_one_way_fuel_cost",
      ]),
    [routeRows],
  );
  const legRows = routeCostResult?.leg_results || [];
  const legColumns = useMemo(
    () => tableColumns(legRows, ["route_id", "leg", "from_stop_sequence", "from_address", "to_stop_sequence", "to_address", "distance_km", "duration_min"]),
    [legRows],
  );

  const busy = previewMutation.isPending || runMutation.isPending || routeCostMutation.isPending;

  return (
    <div className="space-y-6 pb-16 lg:pb-0">
      <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-sm font-medium text-primary">Side tools</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-normal text-foreground">Distance & Cost</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Measure reference-stop distance or estimate current-plan route distance and one-way diesel cost.
          </p>
        </div>
      </section>

      <div className="inline-grid grid-cols-2 rounded-md border border-border bg-muted p-1">
        <ToolTab active={activeTool === "reference"} onClick={() => setActiveTool("reference")}>
          Reference Distance
        </ToolTab>
        <ToolTab active={activeTool === "route_cost"} onClick={() => setActiveTool("route_cost")}>
          Route Cost
        </ToolTab>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <FileSpreadsheet className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold">Workbook</h2>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/60 px-4 py-6 text-center transition hover:border-primary/60 hover:bg-muted">
                <Upload className="mb-3 h-6 w-6 text-primary" aria-hidden="true" />
                <span className="text-sm font-medium">{file?.name || "Select address workbook"}</span>
                <span className="mt-1 text-xs text-muted-foreground">.xlsx or .xlsm</span>
                <input
                  className="sr-only"
                  type="file"
                  accept=".xlsx,.xlsm"
                  onChange={(event) => void handleFileChange(event.target.files?.[0] || null)}
                />
              </label>
              {fileError ? <InlineError message={fileError} /> : null}
              {previewMutation.error ? <InlineError message={(previewMutation.error as Error).message} /> : null}
              <Button
                type="button"
                variant="secondary"
                disabled={!fileBase64 || busy}
                icon={previewMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSpreadsheet className="h-4 w-4" />}
                onClick={() => previewMutation.mutate(undefined)}
              >
                Preview workbook
              </Button>
            </CardContent>
          </Card>

          {preview ? (
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">Columns</h2>
                  <Badge tone="info">{formatNumber(preview.row_count)} rows</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 md:grid-cols-2">
                  <Field label="Sheet">
                    <select className={fieldClassName} value={selectedSheet} onChange={(event) => handleSheetChange(event.target.value)}>
                      {preview.sheet_names.map((sheet) => (
                        <option key={sheet} value={sheet}>
                          {sheet}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {activeTool === "route_cost" ? (
                    <Field label="Route Column">
                      <select className={fieldClassName} value={routeColumn} onChange={(event) => {
                        setRouteColumn(event.target.value);
                        clearRouteCostResult();
                      }}>
                        <option value="">Select route column</option>
                        {preview.columns.map((column) => (
                          <option key={column} value={column}>
                            {column}
                          </option>
                        ))}
                      </select>
                    </Field>
                  ) : null}
                  <Field label="Address Column">
                    <select className={fieldClassName} value={addressColumn} onChange={(event) => handleAddressColumnChange(event.target.value)}>
                      {preview.columns.map((column) => (
                        <option key={column} value={column}>
                          {column}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {activeTool === "route_cost" ? (
                    <>
                      <OptionalColumnField label="Stop Order Column" value={sequenceColumn} columns={preview.columns} emptyLabel="Use row order" onChange={(value) => {
                        setSequenceColumn(value);
                        clearRouteCostResult();
                      }} />
                      <OptionalColumnField label="Bus Type Column" value={busTypeColumn} columns={preview.columns} emptyLabel="No bus type column" onChange={(value) => {
                        setBusTypeColumn(value);
                        clearRouteCostResult();
                      }} />
                    </>
                  ) : null}
                  <OptionalColumnField label="City Column" value={cityColumn} columns={preview.columns} onChange={(value) => {
                    setCityColumn(value);
                    setResult(null);
                    setRouteCostResult(null);
                  }} />
                  <OptionalColumnField label="Country Column" value={countryColumn} columns={preview.columns} onChange={(value) => {
                    setCountryColumn(value);
                    setResult(null);
                    setRouteCostResult(null);
                  }} />
                </div>
                <DataPreview rows={preview.sample_rows} />
              </CardContent>
            </Card>
          ) : null}

          {activeTool === "reference" && result ? (
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">Results</h2>
                  <Badge tone="success">{formatNumber(result.summary.resolved_count)} resolved</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 md:grid-cols-4">
                  <Metric label="Rows" value={result.summary.row_count} />
                  <Metric label="Resolved" value={result.summary.resolved_count} />
                  <Metric label="Failed" value={result.summary.failed_count} />
                  <Metric label="Blank" value={result.summary.blank_count} />
                </div>
                <ResultTable rows={resultRows} columns={resultColumns} />
              </CardContent>
            </Card>
          ) : null}

          {activeTool === "route_cost" && routeCostResult ? (
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">Route Cost Results</h2>
                  <Badge tone="success">{formatNumber(routeCostResult.summary.route_count)} routes</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 md:grid-cols-4">
                  <Metric label="One-Way Distance km" value={routeCostResult.summary.total_one_way_distance_km} />
                  <Metric
                    label={`Diesel Cost ${routeCostResult.summary.currency_label}`}
                    value={formatCurrency(routeCostResult.summary.estimated_one_way_fuel_cost, routeCostResult.summary.currency_code)}
                  />
                  <Metric label="Routes With Failed Stops" value={routeCostResult.summary.routes_with_unresolved_stops} />
                  <Metric label="Electric Routes Skipped" value={routeCostResult.summary.electric_routes_skipped} />
                </div>
                <ResultTable rows={routeRows} columns={routeColumns} />
                {legRows.length ? (
                  <details className="rounded-md border border-border bg-muted/40">
                    <summary className="cursor-pointer px-3 py-3 text-sm font-semibold">Leg-by-leg details</summary>
                    <div className="border-t border-border">
                      <ResultTable rows={legRows} columns={legColumns} />
                    </div>
                  </details>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </div>

        <aside className="space-y-4">
          {activeTool === "reference" ? (
            <>
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <MapPinned className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">Reference Stop</h2>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <Field label="Country">
                    <input className={fieldClassName} value={originCountry} onChange={(event) => {
                      setOriginCountry(event.target.value);
                      clearDistanceResult();
                    }} />
                  </Field>
                  <Field label="City">
                    <input className={fieldClassName} value={originCity} onChange={(event) => {
                      setOriginCity(event.target.value);
                      clearDistanceResult();
                    }} />
                  </Field>
                  <Field label="Address">
                    <textarea className={textareaClassName} value={originAddress} onChange={(event) => {
                      setOriginAddress(event.target.value);
                      clearDistanceResult();
                    }} />
                  </Field>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Ruler className="h-4 w-4 text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">Mode</h2>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-2 gap-2">
                    <ModeButton active={distanceMode === "road"} onClick={() => {
                      setDistanceMode("road");
                      clearDistanceResult();
                    }}>
                      Road
                    </ModeButton>
                    <ModeButton active={distanceMode === "straight_line"} onClick={() => {
                      setDistanceMode("straight_line");
                      clearDistanceResult();
                    }}>
                      Straight
                    </ModeButton>
                  </div>
                  {runMutation.error ? <InlineError message={(runMutation.error as Error).message} /> : null}
                  <Button
                    type="button"
                    disabled={!preview || !originAddress.trim() || busy}
                    icon={runMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Ruler className="h-4 w-4" />}
                    onClick={() => runMutation.mutate()}
                  >
                    Run check
                  </Button>
                </CardContent>
              </Card>
            </>
          ) : (
            <Card>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <Fuel className="h-4 w-4 text-primary" aria-hidden="true" />
                  <h2 className="text-sm font-semibold">Route Cost Settings</h2>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <Field label="Market">
                  <select className={fieldClassName} value={routeCostProfileKey} onChange={(event) => setRouteCostProfileKey(event.target.value as keyof typeof routeCostProfiles)}>
                    {Object.entries(routeCostProfiles).map(([key, profile]) => (
                      <option key={key} value={key}>
                        {profile.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Default Country">
                  <input className={fieldClassName} value={routeDefaultCountry} onChange={(event) => {
                    setRouteDefaultCountry(event.target.value);
                    clearRouteCostResult();
                  }} />
                </Field>
                <Field label="Default City">
                  <input className={fieldClassName} value={routeDefaultCity} onChange={(event) => {
                    setRouteDefaultCity(event.target.value);
                    clearRouteCostResult();
                  }} />
                </Field>
                <Field label={`Diesel Price (${routeCostProfile.currencyCode}/L)`}>
                  <input
                    className={fieldClassName}
                    type="number"
                    min="0"
                    step={routeCostProfile.dieselStep}
                    value={dieselPrice}
                    onChange={(event) => {
                      setDieselPrice(Number(event.target.value));
                      clearRouteCostResult();
                    }}
                  />
                </Field>
                <Field label="Fuel Efficiency (km/L)">
                  <input
                    className={fieldClassName}
                    type="number"
                    min="0.1"
                    step="0.1"
                    value={fuelEfficiency}
                    onChange={(event) => {
                      setFuelEfficiency(Number(event.target.value));
                      clearRouteCostResult();
                    }}
                  />
                </Field>
                <p className="text-xs leading-5 text-muted-foreground">
                  Electric, e-bus, EV, and new-energy bus types keep distance results but skip diesel-cost estimation.
                </p>
                {routeCostMutation.error ? <InlineError message={(routeCostMutation.error as Error).message} /> : null}
                <Button
                  type="button"
                  disabled={!preview || !routeColumn || !addressColumn || busy}
                  icon={routeCostMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Calculator className="h-4 w-4" />}
                  onClick={() => routeCostMutation.mutate()}
                >
                  Calculate cost
                </Button>
              </CardContent>
            </Card>
          )}
        </aside>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function ToolTab({ active, children, onClick }: { active: boolean; children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      className={cn(
        "h-9 rounded px-3 text-sm font-medium transition",
        active ? "bg-surface text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function OptionalColumnField({
  label,
  value,
  columns,
  emptyLabel = "Use reference value",
  onChange,
}: {
  label: string;
  value: string;
  columns: string[];
  emptyLabel?: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label}>
      <select className={fieldClassName} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{emptyLabel}</option>
        {columns.map((column) => (
          <option key={column} value={column}>
            {column}
          </option>
        ))}
      </select>
    </Field>
  );
}

function ModeButton({ active, children, onClick }: { active: boolean; children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      className={cn(
        "h-9 rounded-md border px-3 text-sm font-medium transition",
        active ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface text-foreground hover:bg-muted",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-muted/50 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold">{typeof value === "number" ? formatNumber(value) : value}</div>
    </div>
  );
}

function ResultTable({ rows, columns }: { rows: Array<Record<string, unknown>>; columns: string[] }) {
  if (!rows.length || !columns.length) {
    return null;
  }
  return (
    <div className="overflow-auto rounded-md border border-border">
      <table className="min-w-full divide-y divide-border text-left text-sm">
        <thead className="bg-muted text-xs text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column} className="whitespace-nowrap px-3 py-2 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.slice(0, 100).map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="max-w-72 truncate px-3 py-2">
                  {formatCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DataPreview({ rows }: { rows: Array<Record<string, unknown>> }) {
  const columns = Object.keys(rows[0] || {}).slice(0, 8);
  if (!rows.length || !columns.length) {
    return null;
  }
  return (
    <div className="overflow-auto rounded-md border border-border">
      <table className="min-w-full divide-y divide-border text-left text-xs">
        <thead className="bg-muted text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column} className="whitespace-nowrap px-3 py-2 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className="max-w-60 truncate px-3 py-2">
                  {formatCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InlineError({ message }: { message: string }) {
  return <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{message}</div>;
}

function tableColumns(rows: Array<Record<string, unknown>>, preferred: string[]) {
  const keys = new Set<string>();
  for (const row of rows.slice(0, 20)) {
    Object.keys(row).forEach((key) => keys.add(key));
  }
  return [...preferred.filter((key) => keys.has(key)), ...[...keys].filter((key) => !preferred.includes(key)).slice(0, 8)];
}

function formatCurrency(value: number, currencyCode: string) {
  if (currencyCode === "KRW") {
    return formatNumber(Math.round(value));
  }
  return formatNumber(Number(value.toFixed(2)));
}

function formatCell(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (typeof value === "number") {
    return formatNumber(value);
  }
  return String(value);
}

async function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = reader.result;
      if (typeof value !== "string") {
        reject(new Error("Workbook could not be read as base64."));
        return;
      }
      resolve(value);
    };
    reader.onerror = () => reject(reader.error || new Error("Workbook could not be read."));
    reader.readAsDataURL(file);
  });
}
