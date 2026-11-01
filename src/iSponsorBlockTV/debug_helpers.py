class AiohttpTracer:
    def __init__(self, logger):
        self.logger = logger

    async def on_request_start(self, session, context, params):
        self.logger.debug(f"Request started ({id(context):#x}): {params.method} {params.url}")

    async def on_request_end(self, session, context, params):
        self.logger.debug(f"Request ended ({id(context):#x}): {params.response.status}")

    async def on_request_exception(self, session, context, params):
        self.logger.debug(f"Request exception ({id(context):#x}): {params.exception}")

    async def on_response_chunk_received(self, session, context, params):
        chunk_size = len(params.chunk)
        self.logger.debug("Response chunk (%#x): %d bytes", id(context), chunk_size)
