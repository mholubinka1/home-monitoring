from urllib.parse import quote

import requests
from pydantic import BaseModel

from hive_app.data.weather_types import REQUEST_TIMEOUT_SECONDS, FiniteFloat


class PostcodesIoResult(BaseModel):
    latitude: FiniteFloat
    longitude: FiniteFloat


class PostcodesIoResponse(BaseModel):
    result: PostcodesIoResult


class IpWhoResponse(BaseModel):
    success: bool
    latitude: FiniteFloat | None = None
    longitude: FiniteFloat | None = None


class GeocodingClient:
    postcodes_io_url: str = "https://api.postcodes.io/postcodes"
    ipwho_url: str = "https://ipwho.is/"

    def geolocate_ip(self) -> tuple[float, float]:
        response = requests.get(url=self.ipwho_url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        parsed = IpWhoResponse.model_validate(response.json())
        if not parsed.success or parsed.latitude is None or parsed.longitude is None:
            raise ValueError("ipwho.is returned no location.")
        return parsed.latitude, parsed.longitude

    def geocode_postcode(self, postcode: str) -> tuple[float, float]:
        try:
            response = requests.get(
                url=f"{self.postcodes_io_url}/{quote(postcode, safe='')}",
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            # The exception's message and .response.url embed the request URL,
            # which carries the household postcode. Re-raised with only the
            # status code, `from None`, so the postcode never reaches a log.
            status = (
                e.response.status_code
                if isinstance(e, requests.HTTPError) and e.response is not None
                else "unknown"
            )
            raise RuntimeError(
                f"postcodes.io request failed with status {status}."
            ) from None
        result = PostcodesIoResponse.model_validate(response.json()).result
        return result.latitude, result.longitude
