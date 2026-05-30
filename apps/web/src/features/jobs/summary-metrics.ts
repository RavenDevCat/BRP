export function jobInputStopCount(summary: Record<string, unknown>) {
  const explicitInputStopCount = Number(summary.input_record_count);
  if (!Number.isFinite(explicitInputStopCount)) {
    return explicitInputStopCount;
  }
  if ("input_point_count" in summary) {
    return explicitInputStopCount;
  }
  return Math.max(0, explicitInputStopCount - 1);
}

export function currentPlanAssignmentCount(summary: Record<string, unknown>) {
  const explicitAssignmentCount = Number(summary.current_plan_assignment_count);
  if (!Number.isFinite(explicitAssignmentCount)) {
    return explicitAssignmentCount;
  }
  if ("current_plan_scheduled_assignment_count" in summary) {
    return explicitAssignmentCount;
  }
  const routeCount = Number(summary.current_plan_route_count);
  return Math.max(0, explicitAssignmentCount - (Number.isFinite(routeCount) ? routeCount : 0));
}
