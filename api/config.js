export const config = { runtime: 'edge' };

// Hands the page the address of the Sound Bender processing server on Railway.
export default async function handler() {
  const server = (process.env.SOUND_BENDER_SERVER || '').trim().replace(/\/+$/, '');
  const full = server && !/^https?:\/\//.test(server) ? 'https://' + server : server;
  return new Response(JSON.stringify({ server: full || null }), {
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' }
  });
}
