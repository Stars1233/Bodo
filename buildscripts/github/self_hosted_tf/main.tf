locals {
  prefix     = "bodo-gh-ci"
  aws_region = "us-east-2"
  version    = "5.6.2"
}


resource "random_password" "random" {
  length = 20
}

resource "aws_resourcegroups_group" "resourcegroups_group" {
  name = "${local.prefix}-group"
  resource_query {
    query = templatefile("${path.module}/templates/resource-group.json", {
      example = local.prefix
    })
  }
}

module "runners" {
  source = "philips-labs/github-runner/aws//modules/multi-runner"
  # Same as local.version
  version = "5.6.2"

  # Multi-Size Runners to Use
  # Assume all Runners are Using Amazon Linux 2023
  # Names & Sizes are Based on CodeBuild Instance Types
  multi_runner_config = {
    "small" = {
      matcherConfig : {
        labelMatchers = [["self-hosted", "small"]]
        exactMatch    = true
      }

      # Recommended disabled for ephemeral runners
      fifo = false

      runner_config = merge(local.base_runner_config, {
        # Instance Type(s) (Multiple Options to Choose for Spot)
        instance_types = ["m6i.large", "m6id.large"]
        # Prefix runners with the environment name
        runner_name_prefix = "${local.prefix}_small_"
        # Max # of Runners of this Size
        runners_maximum_count = 60
      })
    }

    "xlarge" = {
      matcherConfig : {
        labelMatchers = [["self-hosted", "xlarge"]]
        exactMatch    = true
      }

      # Recommended disabled for ephemeral runners
      fifo = false

      runner_config = merge(local.base_runner_config, {
        # Instance Type(s) (Multiple Options to Choose for Spot)
        instance_types = ["c5.18xlarge", "c5n.18xlarge", "c5d.18xlarge"]
        # Prefix runners with the environment name
        runner_name_prefix = "${local.prefix}_xlarge_"
        # Max # of Runners of this Size
        runners_maximum_count = 3
      })
    }
  }

  # General AWS Properties
  aws_region = local.aws_region
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets
  prefix     = local.prefix
  tags = {
    Project = "BodoGHActionsCI"
  }

  # General GitHub Properties
  github_app = {
    id             = var.github_app_id
    key_base64     = var.github_key_base64
    webhook_secret = random_password.random.result
  }

  # Zip Files for Lambdas
  webhook_lambda_zip                = "webhook-${local.version}.zip"
  runner_binaries_syncer_lambda_zip = "runner-binaries-syncer-${local.version}.zip"
  runners_lambda_zip                = "runners-${local.version}.zip"

  # Additional Features
  # Enable debug logging for the lambda functions
  # log_level = "debug"
}

module "webhook_github_app" {
  source     = "philips-labs/github-runner/aws//modules/webhook-github-app"
  version    = "5.6.2"
  depends_on = [module.runners]

  github_app = {
    key_base64     = var.github_key_base64
    id             = var.github_app_id
    webhook_secret = random_password.random.result
  }
  webhook_endpoint = module.runners.webhook.endpoint
}