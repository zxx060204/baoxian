import asyncio

from bullmq import Worker



def create_worker(queue_name, redis_url, tasks, processors, concurrency=1, company_lock=None, refresh_scheduler=None, schedule_service=None, worker_options=None):
    async def consume(job, token):
        if job.name == 'task_state_recovery_check':
            await tasks.reconcile_worker_state()
            return
        if job.name == 'association_refresh_check':
            if refresh_scheduler is None:
                raise ValueError('尚未接入协会自动复查')
            await refresh_scheduler.check()
            return
        if job.name == 'scheduled_index':
            names = ('人寿保险', '年金保险', '健康保险', '意外伤害保险', '委托管理业务')
            categories = {f'PubNewProdTypeCode_{index:02d}': name for index, name in enumerate(names)}
            code = job.data.get('categoryCode')
            schedule_id = job.data.get('scheduleId')
            if not isinstance(schedule_id, int) or isinstance(schedule_id, bool) or schedule_id <= 0:
                raise ValueError('定时计划 ID 无效')
            if schedule_service is None:
                raise ValueError('尚未接入定时计划服务')
            if code not in categories:
                raise ValueError(f'定时任务类别不存在：{code}')
            processor = processors.get('association_index')
            if processor is None:
                raise ValueError('尚未接入协会索引任务')
            async def create_task():
                return await tasks.create_index_task({'code': code, 'name': categories[code]}, enqueue=False, queue_job_id=job.id)
            task = await schedule_service.run_if_enabled(schedule_id, code, create_task)
            if task is not None:
                await processor.process(task['id'])
            return
        if job.name != 'association_index':
            raise ValueError(f'不支持的队列作业：{job.name}')
        task_id = job.data.get('taskId')
        if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id <= 0:
            raise ValueError('任务数据缺少有效 taskId')
        task = await asyncio.to_thread(tasks.get, task_id)
        processor = processors.get(task['type'])
        if processor is None:
            raise ValueError(f"尚未接入任务类型：{task['type']}")
        if task['type'] == 'company_documents':
            company_id = (task.get('scope', {}).get('company') or {}).get('id')
            if not company_id:
                raise ValueError('公司资料任务缺少公司范围')
            if company_lock is None:
                raise ValueError('公司资料任务缺少公司锁')
            release = await company_lock.acquire(company_id)
            try:
                await processor.process(task_id)
            finally:
                await release()
        else:
            await processor.process(task_id)

    options = {'connection': redis_url, 'concurrency': concurrency}
    options.update(worker_options or {})
    return Worker(queue_name, consume, options)
