// Redirect frontend imports of api.js to the headless stub.
const stub = new URL("./frontend_stub_api.mjs", import.meta.url).href;

export async function resolve(specifier, context, nextResolve) {
  const parent = context.parentURL || "";
  const fromFrontend = parent.includes("/frontend/js/");
  const isApi =
    specifier === "../api.js" ||
    specifier === "./api.js" ||
    specifier.endsWith("/js/api.js");
  if (fromFrontend && isApi) {
    return { url: stub, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
