from .fairqueue import FairQueueCog


async def setup(bot):
    await bot.add_cog(FairQueueCog(bot))
