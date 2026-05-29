import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { DashboardPage, JobDetailPage, JobsPage, RootLayout } from "@/app/pages";
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

const jobDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId",
  component: JobDetailPage,
});

const routeTree = rootRoute.addChildren([dashboardRoute, newJobRoute, jobsRoute, jobDetailRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
