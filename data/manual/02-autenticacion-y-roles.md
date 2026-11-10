---
doc_id: autenticacion
title: Autenticación, sesiones y roles
category: seguridad
tags: [jwt, login, permisos, usuarios]
---

# Autenticación, sesiones y roles

> Documentación técnica ficticia, creada con fines educativos para el curso de AI Engineering.

## Inicio de sesión

Los usuarios inician sesión con su correo y contraseña en `POST /api/auth/login`. Si los datos son correctos, la API devuelve un token **JWT** firmado con `JWT_SECRET`, que el frontend guarda en memoria y envía en cada pedido con el encabezado `Authorization: Bearer <token>`.

Las contraseñas se guardan con `bcrypt` (costo 12). Nunca se guardan ni se registran en los logs en texto plano.

## Duración de la sesión

El token tiene una validez de **8 horas** desde el momento del login, pensada para cubrir una jornada laboral completa. Cuando vence, cualquier pedido a la API responde `401` con el código `AUTH-401-EXP` y el frontend redirige a la pantalla de login. No existe un token de renovación (*refresh token*): pasado ese plazo, el usuario tiene que volver a ingresar su contraseña.

Después de 5 intentos fallidos de login en 15 minutos, la cuenta se bloquea durante 30 minutos y la API responde `423` con el código `AUTH-423-LOCK`.

## Roles del sistema

El ERP tiene 10 roles. Cada endpoint declara qué roles pueden usarlo, y la verificación la hace un middleware antes de ejecutar la lógica.

| Rol | Puede |
|---|---|
| `admin` | Todo, incluida la gestión de usuarios y la anulación de comprobantes de días anteriores |
| `gerencia` | Ver todos los reportes y aprobar descuentos mayores al 15 % |
| `encargado_local` | Cerrar la caja, anular ventas del día y aplicar descuentos de hasta 15 % |
| `vendedor` | Crear presupuestos y ventas, y registrar cobros |
| `deposito` | Mover stock, registrar ingresos de mercadería y armar pedidos |
| `compras` | Cargar órdenes de compra y gestionar proveedores |
| `administracion` | Cargar gastos, conciliar bancos y emitir notas de crédito |
| `rrhh` | Gestionar legajos, ausencias y liquidaciones de sueldos |
| `taller` | Ver órdenes de fabricación y actualizar su avance |
| `tienda_online` | Rol técnico, sin acceso al frontend, usado solo por la integración con Tiendanube |

Un usuario tiene un único rol. Si un empleado necesita permisos de dos roles, se le asigna el de mayor alcance.

## Errores de autenticación

| Código | HTTP | Significado |
|---|---|---|
| `AUTH-401-EXP` | 401 | El token venció (pasaron más de 8 horas) |
| `AUTH-401-INV` | 401 | El token es inválido o fue modificado |
| `AUTH-403-ROL` | 403 | El rol del usuario no tiene permiso para ese endpoint |
| `AUTH-423-LOCK` | 423 | Cuenta bloqueada por intentos fallidos |

Un `403` con `AUTH-403-ROL` no es un error del sistema: indica que el usuario intentó una acción que su rol no permite. La solución es pedirle a un administrador que revise el rol asignado.
