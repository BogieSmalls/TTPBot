from aiohttp import web


class FakeRacetimeProvider:
    def __init__(self, *, category="z1rr"):
        self.category = category
        self.runner = None
        self.site = None
        self.origin = None
        self.room_posts = []
        self.webhooks = []
        self.current_races = []
        self.startrace_status = 201
        self.location = "/{}/integration-room".format(category)

    async def start(self):
        application = web.Application()
        application.router.add_post("/o/token", self._token)
        application.router.add_get("/{}/data".format(self.category), self._category)
        application.router.add_post(
            "/o/{}/startrace".format(self.category), self._startrace
        )
        application.router.add_get(
            "/{}/integration-room/data".format(self.category), self._room
        )
        application.router.add_post("/discord-webhook", self._webhook)
        self.runner = web.AppRunner(application)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.origin = "http://127.0.0.1:{}".format(port)
        return self

    async def close(self):
        if self.runner is not None:
            await self.runner.cleanup()

    async def _token(self, request):
        form = await request.post()
        if not form.get("client_id") or not form.get("client_secret"):
            return web.json_response({"error": "invalid_client"}, status=401)
        return web.json_response({"access_token": "fixture-token"})

    async def _category(self, request):
        return web.json_response({
            "slug": self.category,
            "current_races": self.current_races,
        })

    async def _startrace(self, request):
        self.room_posts.append({
            "authorization": request.headers.get("Authorization"),
            "form": dict(await request.post()),
        })
        if self.startrace_status != 201:
            return web.Response(status=self.startrace_status, text="fixture failure")
        room = {
            "name": "{}/integration-room".format(self.category),
            "goal": {"name": "Beat the game"},
            "info_bot": "Triforce Triple Play | Scheduled: fixture",
        }
        if not self.current_races:
            self.current_races.append(room)
        return web.Response(status=201, headers={"Location": self.location})

    async def _room(self, request):
        return web.json_response({
            "name": "{}/integration-room".format(self.category),
            "status": {"value": "open"},
        })

    async def _webhook(self, request):
        self.webhooks.append(await request.json())
        return web.Response(status=204)
