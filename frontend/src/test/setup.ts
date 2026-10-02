import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom doesn't implement scrollIntoView at all — components that call it
// (e.g. scrolling a highlighted row into view) would otherwise throw in
// every test that renders them, regardless of whether the test cares about
// scrolling.
Element.prototype.scrollIntoView ??= () => {};

afterEach(() => cleanup());
