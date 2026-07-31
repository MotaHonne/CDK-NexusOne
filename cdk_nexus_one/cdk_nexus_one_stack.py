from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    aws_bedrock as bedrock,
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
        nexus_agent = bedrock.CfnAgent(
            self, f"NexusOneAgent_{client_name}",
            agent_name=f"NexusONE-{client_name.upper()}-Demo",
            agent_resource_role_arn=agent_role.role_arn,
            instruction=(
                "Eres Nexus ONE, un asistente virtual profesional y eficiente. "
                "Responde las consultas del usuario de forma clara, concisa y amable. "
                "Mantén el contexto de la conversación utilizando la memoria disponible."
            ),
            # Modelo de fundación (Claude 3.5 Sonnet v1)
            foundation_model="anthropic.claude-3-5-sonnet-20240620-v1:0",
            
            # Memoria administrada (retención por 30 días)
            memory_configuration=bedrock.CfnAgent.MemoryConfigurationProperty(
                enabled_session_days=30,
                session_summary_configuration=bedrock.CfnAgent.SessionSummaryConfigurationProperty(
                    max_recent_sessions=10
                )
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
                "AGENT_ID": nexus_agent.attr_agent_id,
                "AGENT_ARN": nexus_agent.attr_agent_arn,
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
                    nexus_agent.attr_agent_arn,
                    f"{nexus_agent.attr_agent_arn}/*"
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
            value=nexus_agent.attr_agent_id,
            description="ID único del Agente Bedrock creado"
        )

        CfnOutput(
            self, "AgentArnOutput",
            value=nexus_agent.attr_agent_arn,
            description="ARN único del Agente Bedrock creado"
        )