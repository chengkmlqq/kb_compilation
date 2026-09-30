import { NextRequest, NextResponse } from "next/server";

/**
 * Pure-frontend API proxy: forwards every /api/* request to the FastAPI
 * backend (api-server), preserving cookies (x-next-identity) and streaming
 * SSE responses for the QA endpoints.
 *
 * The browser talks only to this Next.js origin (same-origin fetch), so the
 * identity cookie stays on the frontend host — same contract as the source
 * platform where Next.js was the only origin the browser saw.
 */

const API_BASE = process.env.API_BASE_URL || "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";

function buildTargetUrl(req: NextRequest): URL {
  const { pathname, search } = req.nextUrl;
  const base = API_BASE.replace(/\/+$/, "");
  return new URL(`${base}${pathname}${search}`);
}

export async function GET(req: NextRequest) {
  return proxy(req);
}

export async function POST(req: NextRequest) {
  return proxy(req);
}

export async function PUT(req: NextRequest) {
  return proxy(req);
}

export async function DELETE(req: NextRequest) {
  return proxy(req);
}

async function proxy(req: NextRequest): Promise<NextResponse> {
  const target = buildTargetUrl(req);

  const headers = new Headers();
  // identity cookie passthrough
  const identityCookie = req.cookies.get("x-next-identity")?.value;
  if (identityCookie) {
    headers.set("cookie", `x-next-identity=${identityCookie}`);
  }
  // content-type for JSON/multipart bodies
  const contentType = req.headers.get("content-type");
  if (contentType) {
    headers.set("content-type", contentType);
  }

  let body: BodyInit | null = null;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = req.body;
  }

  const upstream = await fetch(target.toString(), {
    method: req.method,
    headers,
    body,
    // streaming responses (SSE) must not be buffered by fetch
    duplex: "half",
  } as RequestInit);

  // SSE pass-through: relay the body as a ReadableStream, keep the
  // event-stream headers so the browser parses `data:` lines.
  if (upstream.headers.get("content-type")?.includes("text/event-stream")) {
    const responseHeaders = new Headers();
    responseHeaders.set("content-type", "text/event-stream; charset=utf-8");
    responseHeaders.set("cache-control", "no-cache");
    responseHeaders.set("connection", "keep-alive");
    responseHeaders.set("x-accel-buffering", "no");
    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  }

  const data = await upstream.text();
  const response = new NextResponse(data, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") || "application/json; charset=utf-8",
    },
  });
  return response;
}
