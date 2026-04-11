import { cronJobs } from "convex/server";

import { internal } from "./_generated/api";

const crons = cronJobs();

crons.interval(
  "fail stale chat generations",
  { minutes: 1 },
  internal.chatInternal.failStaleGenerations,
  {},
);

export default crons;
