import { lazy, Suspense, type ReactNode } from "react";
import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { DashboardPage, JobDetailPage, JobsPage, RootLayout } from "@/app/pages";

const AdminStatusPage = lazy(() =>
  import("@/features/admin/admin-status-page").then((module) => ({ default: module.AdminStatusPage })),
);
const DistanceCheckerPage = lazy(() =>
  import("@/features/distance/distance-checker-page").then((module) => ({ default: module.DistanceCheckerPage })),
);
const FleetPlannerPage = lazy(() =>
  import("@/features/fleet/fleet-planner-page").then((module) => ({ default: module.FleetPlannerPage })),
);
const RouteInsertAdvisorPage = lazy(() =>
  import("@/features/insert/route-insert-advisor-page").then((module) => ({ default: module.RouteInsertAdvisorPage })),
);
const NewJobPage = lazy(() =>
  import("@/features/planner/new-job-page").then((module) => ({ default: module.NewJobPage })),
);
const OperationsReviewPage = lazy(() =>
  import("@/features/operations/operations-review-page").then((module) => ({ default: module.OperationsReviewPage })),
);

function lazyRoutePage(render: () => ReactNode) {
  return function LazyRoutePage() {
    return (
      <Suspense fallback={<div className="py-12 text-center text-sm text-muted-foreground">Loading…</div>}>
        {render()}
      </Suspense>
    );
  };
}

const rootRoute = createRootRoute({
  component: RootLayout,
});

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: DashboardPage,
});

const jobsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs",
  component: JobsPage,
});

const newJobRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/new",
  component: lazyRoutePage(() => <NewJobPage />),
});

const distanceCheckerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/distance",
  component: lazyRoutePage(() => <DistanceCheckerPage />),
});

const fleetPlannerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/fleet",
  component: lazyRoutePage(() => <FleetPlannerPage />),
});

const routeInsertAdvisorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/insert-advisor",
  component: lazyRoutePage(() => <RouteInsertAdvisorPage />),
});

const adminRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin",
  component: lazyRoutePage(() => <AdminStatusPage />),
});

const jobDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId",
  component: JobDetailPage,
});

const operationsReviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/operations-review/$jobIds",
  component: lazyRoutePage(() => <OperationsReviewPage />),
});

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  newJobRoute,
  distanceCheckerRoute,
  fleetPlannerRoute,
  routeInsertAdvisorRoute,
  adminRoute,
  jobsRoute,
  jobDetailRoute,
  operationsReviewRoute,
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
