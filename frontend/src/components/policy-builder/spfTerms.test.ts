import { describe, expect, it } from "vitest";
import { describeSpfTerm, ipTerm, isHostname, parseSpf } from "./spfTerms";

describe("describeSpfTerm", () => {
  it("names known services and says what the rest allows", () => {
    expect(describeSpfTerm("include:spf.protection.outlook.com", "Microsoft 365")).toBe("allows Microsoft 365's mail servers");
    expect(describeSpfTerm("include:mail.example.net", null)).toBe("allows the servers listed by mail.example.net");
    expect(describeSpfTerm("ip4:192.0.2.0/24", null)).toBe("allows the address range 192.0.2.0/24");
    expect(describeSpfTerm("mx", null)).toBe("allows your own incoming mail servers (MX)");
    expect(describeSpfTerm("a:web.example.com", null)).toBe("allows the address of web.example.com");
    expect(describeSpfTerm("a/24", null)).toBe("allows the address of the domain itself");
    expect(describeSpfTerm("redirect=_spf.google.com", "Google Workspace")).toBe("uses Google Workspace's SPF record instead of its own");
    expect(describeSpfTerm("~all", null)).toBe("everything else is marked suspicious (softfail)");
    expect(describeSpfTerm("+all", null)).toContain("anyone can send as you");
  });
});

describe("input checks", () => {
  it("accepts addresses and ranges, refuses the rest", () => {
    expect(ipTerm("192.0.2.10")).toBe("ip4:192.0.2.10");
    expect(ipTerm("192.0.2.0/24")).toBe("ip4:192.0.2.0/24");
    expect(ipTerm("2001:DB8::/32")).toBe("ip6:2001:db8::/32");
    expect(ipTerm("2001:db8:0:0:0:0:0:1")).toBe("ip6:2001:db8:0:0:0:0:0:1");
    for (const bad of ["256.0.0.1", "192.0.2.0/33", "2001:db8::1::2", "2001:db8:1", "mail.example.com", "::/129"]) {
      expect(ipTerm(bad)).toBeNull();
    }
  });

  it("accepts include values, including underscores", () => {
    expect(isHostname("_spf.google.com")).toBe(true);
    expect(isHostname("icloud.com")).toBe(true);
    expect(isHostname("not a domain")).toBe(false);
    expect(isHostname("localhost")).toBe(false);
  });
});

describe("parseSpf", () => {
  it("splits a record into what the builder edits and what it keeps as written", () => {
    expect(parseSpf("v=spf1 ip4:192.0.2.1 a mx include:icloud.com exists:%{i}.x.example -all")).toEqual({
      includes: ["icloud.com"],
      ips: ["ip4:192.0.2.1"],
      a: true,
      mx: true,
      others: ["exists:%{i}.x.example"],
      all: "-all",
    });
    expect(parseSpf(null).all).toBe("~all");
    expect(parseSpf("v=spf1 include:x.example ?all").all).toBe("~all");
  });
});
