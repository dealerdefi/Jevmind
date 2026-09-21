# Refresh tokens

Refresh tokens rotate on every use: the old one is marked used and a new one is issued. Reusing a used refresh token revokes every session of that user, because reuse means the token was stolen. Tokens are stored hashed. See [[sessions]] for revocation.
