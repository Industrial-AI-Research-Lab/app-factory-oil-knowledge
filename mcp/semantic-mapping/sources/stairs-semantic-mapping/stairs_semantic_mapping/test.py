import uuid
import pandas as pd

from stairs_sdk.sdk.job_context.utility import construct_job_context
from stairs_sdk.utils.reddis import get_reddis_wrapper, load_result

from stairs_semantic_mapping.mapping_job import JobTasksMapping, JobMeasurementsMapping

job_id = str(uuid.uuid4())
job_name = "measurements_names_mapping"

# electroline_df = pd.read_csv('../examples/electroline_tasks_with_volumes.csv', sep=';')
# electroline_task_names = list(electroline_df[electroline_df['is_service'] == 0]['activity_name'])

measurement_names = ['штук', 'килограмм', 'тонн', 'гектар', 'метров', 'проценты']

ctx = construct_job_context(job_name)

# JobTasksMapping().run(job_id, ctx, names_for_mapping=electroline_task_names, n_results=1)
JobMeasurementsMapping().run(job_id, ctx, names_for_mapping=measurement_names, n_results=1)


rd = get_reddis_wrapper()
result = load_result(rd, job_id, job_name)
print(result)