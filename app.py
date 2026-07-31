#!/usr/bin/env python3
import aws_cdk as cdk
from cdk_nexus_one.cdk_nexus_one_stack import NexusOneBaseStack
from aws_cdk import Tags

app = cdk.App()

# 1. Capturamos la variable 'client' desde la terminal (por defecto será 'demo')
client_name = app.node.try_get_context("client") or "demo"
client_name = client_name.lower() # Normalizamos a minúsculas

# 2. Instanciamos el Stack dándole un nombre único por cliente (ej. NexusOneDemo-Inegi)
stack = NexusOneBaseStack(
    app, 
    f"NexusOneDemo-{client_name.capitalize()}", 
    client_name=client_name  # Le pasamos la variable al Stack
)

# 3. Aplicamos la etiqueta a TODO lo que se cree dentro de este Stack
Tags.of(stack).add("project", client_name)

app.synth()
