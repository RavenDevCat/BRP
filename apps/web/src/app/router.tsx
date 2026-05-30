import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { DashboardPage, JobDetailPage, JobsPage, RootLayout } from "@/app/pages";
import { DistanceCheckerPage } from "@/features/distance/distance-checker-page";
import { FleetPlannerPage } from "@/features/fleet/fleet-planner-page";
import { NewJobPage } from "@/features/planner/new-job-page";

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
  component: NewJobPage,
});

const distanceCheckerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/distance",
  component: DistanceCheckerPage,
});

const fleetPlannerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/fleet",
  component: FleetPlannerPage,
});

const jobDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId",
  component: JobDetailPage,
});

const routeTree = rootRoute.addChildren([dashboardRoute, newJobRoute, distanceCheckerRoute, fleetPlannerRoute, jobsRoute, jobDetailRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
