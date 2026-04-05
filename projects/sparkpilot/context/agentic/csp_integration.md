# CSP Integration — sparkpilot

## Cloud Provider: AWS
- **Region:** us-gov-west-1
- **GovCloud:** No

## Available MCP Servers

| Server | Category | Description |
|--------|----------|-------------|
| @aws/core-mcp-server | core | Core AWS service operations |
| @aws/aws-api-mcp-server | core | AWS API operations |
| @aws/cdk-mcp-server | iac | AWS CDK infrastructure as code |
| @aws/terraform-mcp-server | iac | Terraform for AWS |
| @aws/cloudformation-mcp-server | iac | CloudFormation stack management |
| @aws/iam-mcp-server | security | IAM policy and role management |
| @aws/well-architected-security-mcp-server | security | Well-Architected security review |
| @aws/cloudwatch-mcp-server | monitoring | CloudWatch metrics and logs |
| @aws/cloudtrail-mcp-server | monitoring | CloudTrail audit logging |
| @aws/cost-explorer-mcp-server | monitoring | Cost analysis and optimization |
| @aws/aws-documentation-mcp-server | docs | AWS documentation search |
| @aws/aws-knowledge-mcp-server | docs | AWS knowledge base queries |

## Usage

These MCP servers are configured in `.mcp.json` and available to Claude Code.
Use them for cloud-native operations specific to the target deployment environment.

For capabilities not available via AWS MCP servers, use the A2A
callback to parent ICDEV™.
