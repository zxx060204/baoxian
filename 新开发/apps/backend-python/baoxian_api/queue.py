import logging

from bullmq import Queue

ACTIVE_JOB_STATES = {'waiting', 'active', 'delayed', 'waiting-children', 'paused', 'prioritized'}
TERMINAL_JOB_STATES = {'completed', 'failed'}


class BullMQTaskQueue:
    def __init__(self, redis_url, queue_name='crawl'):
        self.queue = Queue(queue_name, {'connection': redis_url})

    async def enqueue(self, task_id):
        job_id = f'crawl-{task_id}'
        existing = await self.queue.getJob(job_id)
        if existing is not None:
            state = await existing.getState()
            if state in ACTIVE_JOB_STATES:
                return
            await existing.remove()
        await self.queue.add('association_index', {'taskId': task_id}, {'jobId': job_id, 'removeOnComplete': 1000, 'removeOnFail': 1000})

    async def contains_task(self, task_id):
        job = await self.queue.getJob(f'crawl-{task_id}')
        if job is None or job.data.get('taskId') != task_id:
            return False
        return await job.getState() not in TERMINAL_JOB_STATES

    async def get_job_state(self, job_id):
        job = await self.queue.getJob(str(job_id))
        return await job.getState() if job is not None else None

    async def active_scheduled_categories(self):
        jobs = await self.queue.getJobs(['active'])
        return {job.data.get('categoryCode') for job in jobs if job.name == 'scheduled_index' and job.data.get('categoryCode')}

    async def upsert_schedule(self, schedule_id, category_code, every_minutes):
        await self.queue.upsertJobScheduler(
            f'schedule-{schedule_id}', {'every': every_minutes * 60000},
            job_name='scheduled_index',
            job_data={'scheduleId': schedule_id, 'categoryCode': category_code},
            opts={'removeOnComplete': 100, 'removeOnFail': 100},
        )

    async def remove_schedule(self, schedule_id):
        await self.queue.removeJobScheduler(f'schedule-{schedule_id}')

    async def close(self):
        await self.queue.close()


class OnDemandBullMQTaskQueue:
    def __init__(self, redis_url, queue_name='crawl'):
        self.redis_url = redis_url
        self.queue_name = queue_name

    async def _run(self, operation, *args):
        queue = BullMQTaskQueue(self.redis_url, self.queue_name)
        try:
            return await getattr(queue, operation)(*args)
        finally:
            try:
                await queue.close()
            except Exception:
                logging.getLogger(__name__).exception('关闭临时队列连接失败')

    async def enqueue(self, task_id):
        await self._run('enqueue', task_id)

    async def contains_task(self, task_id):
        return await self._run('contains_task', task_id)

    async def get_job_state(self, job_id):
        return await self._run('get_job_state', job_id)

    async def active_scheduled_categories(self):
        return await self._run('active_scheduled_categories')

    async def upsert_schedule(self, schedule_id, category_code, every_minutes):
        await self._run('upsert_schedule', schedule_id, category_code, every_minutes)

    async def remove_schedule(self, schedule_id):
        await self._run('remove_schedule', schedule_id)
