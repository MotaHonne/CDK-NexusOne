from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    aws_bedrockagentcore as bedrock_ac,
)
from constructs import Construct

class NexusOneBaseStack(Stack):
    """
    Stack base desplegable por cliente para el Agente Conversacional Nexus ONE.
    Soporta parametrización mediante 'client_name' y tagging global para FinOps.
    """
    def __init__(self, scope: Construct, construct_id: str, client_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ===================================================================
        # 1. IAM ROLE PARA EL AGENTE DE BEDROCK
        # ===================================================================
        # El rol que asume el servicio Bedrock para invocar modelos (Claude)
        agent_role = iam.Role(
            self, f"NexusOneAgentRole_{client_name}",
            role_name=f"nexus-one-agent-role-{client_name}",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description=f"Rol de ejecución para el agente Bedrock de {client_name}"
        )

        # Permiso explícito para que el agente invoque el modelo en Bedrock
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=["arn:aws:bedrock:*::foundation-model/*"]
            )
        )

        # ===================================================================
        # 2. DEFINICIÓN DEL AGENTE (AWS BEDROCK AGENT)
        # ===================================================================
        # Creamos el agente declarativo con memoria de sesión administrada
        nexus_agent = bedrock_ac.CfnHarness(
            self, f"NexusOneHarness_{client_name}",
            harness_name=f"NexusONE-{client_name.upper()}-Demo",
            execution_role_arn=agent_role.role_arn,
            system_prompt=(
                "Eres Nexus ONE, un asistente virtual profesional y eficiente. "
                "Responde las consultas del usuario de forma clara, concisa y amable. "
                "Mantén el contexto de la conversación utilizando la memoria disponible."
            ),
            # Configuración del modelo base
            model=bedrock_ac.CfnHarness.HarnessModelConfigurationProperty(
                bedrock_model_config=bedrock_ac.CfnHarness.HarnessBedrockModelConfigProperty(
                    model_id="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                    temperature=0.3,
                    max_tokens=2000
                )
            ),
            # Memoria administrada nativa de AgentCore
            memory=bedrock_ac.CfnHarness.HarnessMemoryConfigurationProperty(
                storage_days=30
            )
        )

        # ===================================================================
        # 3. LAMBDA WEBHOOK RECEIVER (PYTHON 3.12)
        # ===================================================================
        webhook_lambda = _lambda.Function(
            self, f"WebhookReceiverLambda_{client_name}",
            function_name=f"webhook-receiver-{client_name}",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="webhook_receiver.lambda_handler",
            code=_lambda.Code.from_asset("src/lambda"),
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                "AGENT_ID": nexus_agent.attr_harness_id,
                "AGENT_ARN": nexus_agent.attr_harness_arn,
                "AGENT_ALIAS_ID": "TSTALIASID",
                "META_VERIFY_TOKEN": "nexus_one_demo_token",
                "META_APP_SECRET": "nexus_one_secret",
                "AWS_REGION_ENV": Stack.of(self).region,
                "SSM_GRAPH_API_TOKEN_NAME": f"/{client_name}/whatsapp/meta/graph-api-token"
            }
        )

        # Permisos para que la Lambda pueda invocar al Agente en Bedrock Runtime
        webhook_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeAgent"],
                resources=[
                    nexus_agent.attr_harness_arn,
                    f"{nexus_agent.attr_harness_arn}/*"
                ]
            )
        )
        webhook_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/{client_name}/whatsapp/*"
                ]
            )
        )

        # ===================================================================
        # 4. API GATEWAY (PUNTO DE ENTRADA HTTP)
        # ===================================================================
        api = apigw.LambdaRestApi(
            self, f"NexusOneWebhookApi_{client_name}",
            rest_api_name=f"webhook-api-{client_name}",
            handler=webhook_lambda,
            proxy=False
        )

        # Creación del endpoint /webhook para Meta
        webhook_resource = api.root.add_resource("webhook")
        
        # GET: Verificación del token de Meta / WhatsApp
        webhook_resource.add_method("GET")
        
        # POST: Recepción de mensajes enviados por los usuarios
        webhook_resource.add_method("POST")

        # ===================================================================
        # 5. OUTPUTS (VALORES DE SALIDA TRAS CDK DEPLOY)
        # ===================================================================
        CfnOutput(
            self, "WebhookUrlOutput",
            value=f"{api.url}webhook",
            description="URL pública del Webhook para configurar en el portal de Meta Developers"
        )

        CfnOutput(
            self, "AgentIdOutput",
            value=nexus_agent.attr_harness_id,
            description="ID único del Agente Bedrock creado"
        )

        CfnOutput(
            self, "AgentArnOutput",
            value=nexus_agent.attr_harness_arn,
            description="ARN único del Agente Bedrock creado"
        )