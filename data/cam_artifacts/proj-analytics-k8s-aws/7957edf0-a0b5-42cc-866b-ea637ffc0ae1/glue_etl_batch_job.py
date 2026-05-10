# CUI // SP-CTI
# AWS Glue PySpark ETL job — replaces NiFi batch flow.
# Deploy via AWS Glue Studio or `aws glue create-job`.
import sys
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql.functions import col, current_timestamp

args = getResolvedOptions(sys.argv, ["JOB_NAME", "source_database",
                                      "source_table", "target_path"])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["JOB_NAME"], args)

# --- Extract ---
source_df = glueContext.create_dynamic_frame.from_catalog(
    database=args["source_database"],
    table_name=args["source_table"],
).toDF()

# --- Transform (replace with actual business logic) ---
transformed_df = (
    source_df
    .filter(col("is_active") == True)
    .withColumn("etl_timestamp", current_timestamp())
)

# --- Load ---
glueContext.write_dynamic_frame.from_options(
    frame=DynamicFrame.fromDF(transformed_df, glueContext, "output"),
    connection_type="s3",
    connection_options={"path": args["target_path"], "partitionKeys": ["etl_timestamp"]},
    format="parquet",
)
job.commit()
