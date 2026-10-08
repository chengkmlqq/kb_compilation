declare module "popoto" {
  const popoto: {
    init: () => void;
    graph: (containerId: string, opts?: Record<string, unknown>) => unknown;
    taxonomy: (containerId: string) => unknown;
    query: (containerId: string) => unknown;
    result: (containerId: string) => unknown;
    provider: Record<string, unknown>;
    runner: {
      run: (statements: unknown) => Promise<unknown>;
      toObject: (results: unknown) => unknown;
    };
  };
  export default popoto;
}