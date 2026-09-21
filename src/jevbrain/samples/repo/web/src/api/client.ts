export async function refresh(token: string) { return post("/auth/refresh", { token }) }
